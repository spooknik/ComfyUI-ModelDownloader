"""Live system statistics for the System tab: CPU, RAM, GPUs, disks, versions and ComfyUI's own state.

Everything is best-effort. psutil, pynvml (the `nvidia-ml-py` package) and torch are all optional: a section whose
source is missing or fails becomes None (or an empty list) and the reason goes under `notes`, so `collect()` never
raises. `collect()` blocks briefly on syscalls and NVML, so call it from an executor thread. It is polled every
couple of seconds while the tab is open, so it only does cheap reads and caches anything static.
"""

from __future__ import annotations

import functools
import importlib
import importlib.metadata
import logging
import os
import platform
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import restart

try:
    import psutil
except ImportError:  # ComfyUI requires psutil, but don't break the plugin without it.
    psutil = None

logger = logging.getLogger(__name__)

# Random per process start. The browser compares it before and after a restart to tell the new process from the old.
BOOT_ID = uuid.uuid4().hex
# When this process (re)started. Not psutil's create_time(): os.execv keeps the PID and its creation time on Linux.
STARTED_AT = time.time()

CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_SELF_CGROUP = Path("/proc/self/cgroup")
DISK_ROLES = ("models", "output", "input", "temp")

_cache: dict[str, Any] = {}
_cache_lock = threading.RLock()  # Re-entrant: cached computations may use other cached values.


def reset_caches() -> None:
    """Forget cached static facts (CPU model, versions, NVML state). For tests."""
    with _cache_lock:
        _cache.clear()


def _cached(key: str, compute: Callable[[], Any]) -> Any:
    with _cache_lock:
        if key not in _cache:
            _cache[key] = compute()
        return _cache[key]


def _attempt(fn: Callable[..., Any], *args: Any) -> Any:
    """`fn(*args)`, or None if it raises (e.g. NVML's NotSupported for fan speed on laptops)."""
    try:
        return fn(*args)
    except Exception:
        return None


def _text(value: Any) -> str | None:
    if isinstance(value, bytes):  # Older pynvml returns bytes.
        return value.decode(errors="replace")
    return None if value is None else str(value)


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


def _read_int(path: Path) -> int | None:
    text = _read(path)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


class _CpuSampler:
    """CPU busy % since the previous poll, like `psutil.cpu_percent(interval=None)`, but with one shared sample.

    psutil >= 6 keeps cpu_percent's previous sample per calling thread, and stats are collected on whichever
    executor thread is free, so the first call on each thread would read 0. Keeping the cpu_times() snapshot
    here makes the interval simply "since the last poll", whatever thread runs it.
    """

    MIN_INTERVAL = 0.5  # Two browser tabs polling at once would otherwise measure a few ms of noise.

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.taken_at = 0.0
        self.times: Any = None
        self.per_cpu: Any = None
        self.result: tuple[float | None, list[float]] = (None, [])

    @staticmethod
    def _busy_percent(before: Any, after: Any) -> float:
        def total(t: Any) -> float:
            # Linux counts guest time inside user/nice too (same correction as psutil).
            return sum(t) - getattr(t, "guest", 0) - getattr(t, "guest_nice", 0)

        def busy(t: Any) -> float:
            return total(t) - t.idle - getattr(t, "iowait", 0)

        elapsed = total(after) - total(before)
        if elapsed <= 0:
            return 0.0
        return round(min(max(100 * (busy(after) - busy(before)) / elapsed, 0.0), 100.0), 1)

    def sample(self) -> tuple[float | None, list[float]]:
        """(overall %, per-core %) since the previous sample; (None, []) on the very first one."""
        with self.lock:
            now = time.monotonic()
            if self.result[0] is not None and now - self.taken_at < self.MIN_INTERVAL:
                return self.result
            times, per_cpu = psutil.cpu_times(), psutil.cpu_times(percpu=True)
            if self.times is not None:
                self.result = (
                    self._busy_percent(self.times, times),
                    [self._busy_percent(a, b) for a, b in zip(self.per_cpu, per_cpu, strict=False)],
                )
            self.taken_at, self.times, self.per_cpu = now, times, per_cpu
            return self.result


_cpu_sampler = _CpuSampler()


def prime() -> None:
    """Take the first CPU sample now, so the first poll already gets a real CPU % (not a meaningless 0)."""
    if psutil is not None:
        _attempt(_cpu_sampler.sample)


# ---- cgroup limits (containers) ---------------------------------------------------------------------------------


def _cgroup_v2_dir(root: Path, proc_self_cgroup: Path) -> Path | None:
    if not (root / "cgroup.controllers").exists():
        return None
    # Docker's default private cgroup namespace mounts the container's own cgroup at the root. With a host
    # namespace, /proc/self/cgroup ("0::/system.slice/docker-<id>.scope") points at the right sub-directory.
    for line in (_read(proc_self_cgroup) or "").splitlines():
        if line.startswith("0::"):
            candidate = root / line[3:].strip().lstrip("/")
            if candidate != root and candidate.is_dir():
                return candidate
    return root


def cgroup_limits(
    host_memory: int | None, root: Path = CGROUP_ROOT, proc_self_cgroup: Path = PROC_SELF_CGROUP
) -> dict[str, Any] | None:
    """Container memory/CPU limits from cgroup v2 or v1, or None if there are none (or not on Linux).

    A memory limit at or above the host's RAM counts as no limit (v1 reports "unlimited" as ~2**63).
    `memory_used` is the working set like `docker stats` shows: usage minus inactive page cache.
    """
    memory_limit = memory_usage = inactive_file = None
    cpu_limit = None
    version = None

    v2 = _cgroup_v2_dir(root, proc_self_cgroup)
    if v2 is not None:
        version = 2
        memory_limit = _read_int(v2 / "memory.max")  # "max" -> None
        memory_usage = _read_int(v2 / "memory.current")
        inactive_key = "inactive_file"
        memory_stat = v2 / "memory.stat"
        cpu_max = (_read(v2 / "cpu.max") or "").split()
        if len(cpu_max) == 2 and cpu_max[0] != "max":
            try:
                cpu_limit = int(cpu_max[0]) / int(cpu_max[1])
            except (ValueError, ZeroDivisionError):
                cpu_limit = None
    elif (root / "memory").is_dir() or (root / "cpu").is_dir() or (root / "cpu,cpuacct").is_dir():
        version = 1
        memory_limit = _read_int(root / "memory" / "memory.limit_in_bytes")
        memory_usage = _read_int(root / "memory" / "memory.usage_in_bytes")
        inactive_key = "total_inactive_file"
        memory_stat = root / "memory" / "memory.stat"
        for cpu_dir in (root / "cpu", root / "cpu,cpuacct"):
            quota = _read_int(cpu_dir / "cpu.cfs_quota_us")
            period = _read_int(cpu_dir / "cpu.cfs_period_us")
            if quota is not None and period:
                cpu_limit = quota / period if quota > 0 else None
                break
    else:
        return None

    if memory_limit is not None and host_memory and memory_limit >= host_memory:
        memory_limit = None
    if memory_usage is not None:
        for line in (_read(memory_stat) or "").splitlines():
            key, _, value = line.partition(" ")
            if key == inactive_key and value.strip().isdigit():
                inactive_file = int(value)
                break
    memory_used = max(memory_usage - (inactive_file or 0), 0) if memory_usage is not None else None
    if memory_limit is None and cpu_limit is None:
        return None
    return {
        "version": version,
        "memory_limit": memory_limit,
        "memory_used": memory_used,
        "memory_percent": (
            round(100 * memory_used / memory_limit, 1) if memory_limit and memory_used is not None else None
        ),
        "cpu_limit": round(cpu_limit, 2) if cpu_limit else None,
    }


# ---- CPU and RAM ------------------------------------------------------------------------------------------------


def _cpu_model() -> str | None:
    if sys.platform.startswith("linux"):
        for line in (_read(Path("/proc/cpuinfo")) or "").splitlines():
            if line.lower().startswith(("model name", "hardware", "processor\t: ")) and ":" in line:
                name = line.split(":", 1)[1].strip()
                if name and not name.isdigit():
                    return name
    elif sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            with key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or None


def _cpu_static() -> dict[str, Any]:
    usable = None
    if hasattr(os, "sched_getaffinity"):
        usable = _attempt(lambda: len(os.sched_getaffinity(0)))
    return {
        "model": _attempt(_cpu_model),
        "logical": psutil.cpu_count(logical=True),
        "physical": _attempt(lambda: psutil.cpu_count(logical=False)),
        "usable": usable,
    }


def cpu_stats(limits: dict[str, Any] | None) -> dict[str, Any]:
    load_avg = None
    if hasattr(os, "getloadavg"):  # Not psutil.getloadavg(): on Windows it starts a sampling thread.
        load_avg = _attempt(lambda: [round(x, 2) for x in os.getloadavg()])
    percent, per_core = _cpu_sampler.sample()
    return {
        **_cached("cpu_static", _cpu_static),
        "percent": percent,
        "per_core": per_core,
        "load_avg": load_avg,
        "cgroup_limit": limits["cpu_limit"] if limits else None,
    }


def ram_stats(limits: dict[str, Any] | None) -> dict[str, Any]:
    vm = psutil.virtual_memory()
    swap = _attempt(psutil.swap_memory)
    process = _cached("process", psutil.Process)
    cgroup = None
    if limits and limits["memory_limit"]:
        cgroup = {k: limits[k] for k in ("memory_limit", "memory_used", "memory_percent")}
    return {
        "total": vm.total,
        "used": vm.total - vm.available,
        "available": vm.available,
        "percent": vm.percent,
        "swap_total": swap.total if swap else None,
        "swap_used": swap.used if swap else None,
        "swap_percent": swap.percent if swap else None,
        "process_rss": _attempt(lambda: process.memory_info().rss),
        "cgroup": cgroup,
    }


# ---- GPUs -------------------------------------------------------------------------------------------------------


def _nvml() -> tuple[Any, str | None]:
    """The initialised pynvml module, or (None, reason). Initialised once per process; failures are cached too."""

    def init() -> tuple[Any, str | None]:
        try:
            import pynvml
        except ImportError:
            return None, "nvidia-ml-py is not installed (pip install nvidia-ml-py for GPU load, temperature and power)"
        try:
            pynvml.nvmlInit()
        except Exception as exc:  # No NVIDIA driver / GPU, or NVML not reachable in this container.
            return None, f"NVML unavailable: {exc}"
        return pynvml, None

    return _cached("nvml", init)


def _nvml_identity(nv: Any, handle: Any) -> dict[str, Any]:
    return {
        "name": _text(_attempt(nv.nvmlDeviceGetName, handle)),
        "uuid": _text(_attempt(nv.nvmlDeviceGetUUID, handle)),
    }


def _nvml_gpus(nv: Any) -> list[dict[str, Any]]:
    gpus = []
    for index in range(nv.nvmlDeviceGetCount()):
        handle = nv.nvmlDeviceGetHandleByIndex(index)
        static = _cached(f"nvml_device_{index}", functools.partial(_nvml_identity, nv, handle))
        memory = _attempt(nv.nvmlDeviceGetMemoryInfo, handle)
        utilization = _attempt(nv.nvmlDeviceGetUtilizationRates, handle)
        power = _attempt(nv.nvmlDeviceGetPowerUsage, handle)  # milliwatts
        power_limit = _attempt(nv.nvmlDeviceGetEnforcedPowerLimit, handle)
        gpus.append(
            {
                "index": index,
                **static,
                "source": "nvml",
                "util_percent": utilization.gpu if utilization is not None else None,
                "mem_used": memory.used if memory is not None else None,
                "mem_total": memory.total if memory is not None else None,
                "temperature_c": _attempt(nv.nvmlDeviceGetTemperature, handle, nv.NVML_TEMPERATURE_GPU),
                "power_w": round(power / 1000, 1) if power is not None else None,
                "power_limit_w": round(power_limit / 1000, 1) if power_limit is not None else None,
                "fan_percent": _attempt(nv.nvmlDeviceGetFanSpeed, handle),
                "torch": None,
            }
        )
    return gpus


def _torch_devices() -> tuple[list[dict[str, Any]], str | None]:
    """PyTorch's view of each CUDA device, without ever initialising CUDA ourselves (ComfyUI already has)."""
    torch = sys.modules.get("torch")  # Never import torch here: it is slow, and ComfyUI has imported it already.
    if torch is None:
        return [], "PyTorch is not loaded"
    cuda = getattr(torch, "cuda", None)
    try:
        if cuda is None or not cuda.is_initialized():
            return [], "CUDA is not initialised in this process"
        count = cuda.device_count()
    except Exception as exc:
        return [], f"PyTorch CUDA query failed: {exc}"

    devices = []
    for index in range(count):
        props = _attempt(cuda.get_device_properties, index)
        reserved = _attempt(cuda.memory_reserved, index)
        free = total = None
        # mem_get_info creates a CUDA context (hundreds of MB of VRAM) on a device that has none yet. Only ask about
        # the sole device, or devices where PyTorch already holds memory (so a context exists).
        if count == 1 or reserved:
            info = _attempt(cuda.mem_get_info, index)
            if info is not None:
                free, total = info
        devices.append(
            {
                "index": index,
                "name": getattr(props, "name", None),
                "uuid": _text(getattr(props, "uuid", None)),
                "allocated": _attempt(cuda.memory_allocated, index),
                "reserved": reserved,
                "free": free,
                "total": total if total is not None else getattr(props, "total_memory", None),
            }
        )
    return devices, None


def _uuid_key(value: str | None) -> str | None:
    if not value:
        return None
    value = value.lower()
    return value[4:] if value.startswith("gpu-") else value


def gpu_stats(notes: dict[str, str]) -> list[dict[str, Any]]:
    nv, nvml_error = _nvml()
    gpus: list[dict[str, Any]] = []
    if nv is not None:
        try:
            gpus = _nvml_gpus(nv)
        except Exception as exc:
            nvml_error = f"NVML query failed: {exc}"
    torch_devices, torch_error = _torch_devices()

    # Attach PyTorch's numbers to the matching NVML GPU. NVML lists every GPU in PCI order; PyTorch only the visible
    # ones (CUDA_VISIBLE_DEVICES) in its own order, so match by UUID (or trivially when there is one of each).
    by_uuid = {_uuid_key(g["uuid"]): g for g in gpus if g["uuid"]}
    unmatched = []
    for device in torch_devices:
        match = by_uuid.get(_uuid_key(device["uuid"]))
        if match is None and len(gpus) == 1 and len(torch_devices) == 1:
            match = gpus[0]
        if match is not None and match["torch"] is None:
            match["torch"] = device
        else:
            unmatched.append(device)

    for device in unmatched:  # No NVML (not installed, AMD/ROCm, ...): PyTorch is all we have.
        used = device["total"] - device["free"] if device["total"] is not None and device["free"] is not None else None
        gpus.append(
            {
                "index": device["index"],
                "name": device["name"],
                "uuid": device["uuid"],
                "source": "torch",
                "util_percent": None,
                "mem_used": used,
                "mem_total": device["total"],
                "temperature_c": None,
                "power_w": None,
                "power_limit_w": None,
                "fan_percent": None,
                "torch": device,
            }
        )

    if nvml_error and not gpus:
        notes["gpus"] = nvml_error if not torch_error else f"{nvml_error}; {torch_error}"
    elif nvml_error:
        notes["gpus"] = nvml_error
    return gpus


# ---- Disks ------------------------------------------------------------------------------------------------------


def _folder_paths() -> Any:
    try:
        import folder_paths

        return folder_paths
    except Exception:
        return None


def role_directories(comfyui_base: Path) -> dict[str, Path]:
    """Where ComfyUI keeps models, output, input and temp (honours --base-directory / --output-directory etc.)."""
    fp = _folder_paths()
    found: dict[str, Any] = {
        "models": getattr(fp, "models_dir", None),
        "output": _attempt(getattr(fp, "get_output_directory", None)),
        "input": _attempt(getattr(fp, "get_input_directory", None)),
        "temp": _attempt(getattr(fp, "get_temp_directory", None)),
    }
    return {role: Path(found[role]) if found[role] else Path(comfyui_base) / role for role in DISK_ROLES}


def _nearest_existing(path: Path) -> Path | None:
    for candidate in (path, *path.parents):  # temp/ may not exist yet; its parent's filesystem is the one it'll use.
        if candidate.exists():
            return candidate
    return None


def disk_stats(comfyui_base: Path) -> list[dict[str, Any]]:
    """One entry per filesystem: directories on the same device (same st_dev) share an entry and its roles label."""
    disks: dict[int, dict[str, Any]] = {}
    for role, path in role_directories(comfyui_base).items():
        existing = _nearest_existing(path)
        if existing is None:
            continue
        try:
            device = os.stat(existing).st_dev
            if device in disks:
                disks[device]["roles"].append(role)
                disks[device]["paths"][role] = str(path)
                continue
            usage = shutil.disk_usage(existing)
        except OSError:
            continue
        disks[device] = {
            "roles": [role],
            "paths": {role: str(path)},
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(100 * usage.used / usage.total, 1) if usage.total else None,
        }
    for disk in disks.values():
        disk["label"] = ", ".join(disk["roles"])
    return list(disks.values())


# ---- Versions ---------------------------------------------------------------------------------------------------


def _dist_version(*names: str) -> str | None:
    for name in names:
        try:
            return importlib.metadata.version(name)
        except Exception:
            continue
    return None


def _comfyui_version() -> str | None:
    module = sys.modules.get("comfyui_version")
    if module is None:
        try:
            module = importlib.import_module("comfyui_version")
        except Exception:
            return None
    return _text(getattr(module, "__version__", None))


def _os_name() -> str:
    if sys.platform.startswith("linux"):
        for line in (_read(Path("/etc/os-release")) or "").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    return platform.platform(terse=True)


def detect_container() -> str | None:
    """Container runtime hint ("docker", "podman", "kubernetes", ...), or None if not apparently in one."""
    if os.path.exists("/.dockerenv"):
        return "docker"
    if os.path.exists("/run/.containerenv"):
        return "podman"
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    cgroup = _read(Path("/proc/1/cgroup")) or ""
    hints = {"kubepods": "kubernetes", "docker": "docker", "libpod": "podman", "containerd": "containerd", "lxc": "lxc"}
    for hint, name in hints.items():
        if hint in cgroup:
            return name
    return os.environ.get("container") or None


def _nvidia_driver() -> str | None:
    nv, _ = _nvml()
    if nv is not None:
        version = _text(_attempt(nv.nvmlSystemGetDriverVersion))
        if version:
            return version
    # Linux without nvidia-ml-py: "NVRM version: NVIDIA UNIX x86_64 Kernel Module  550.54.14  ..."
    words = (_read(Path("/proc/driver/nvidia/version")) or "").split()
    if "Module" in words and words.index("Module") + 1 < len(words):
        return words[words.index("Module") + 1]
    return None


def _versions() -> dict[str, Any]:
    torch = sys.modules.get("torch")
    torch_version = cuda = cudnn = hip = None
    if torch is not None:
        torch_version = _text(getattr(torch, "__version__", None))
        cuda = _text(getattr(getattr(torch, "version", None), "cuda", None))
        hip = _text(getattr(getattr(torch, "version", None), "hip", None))
        cudnn = _attempt(lambda: torch.backends.cudnn.version())
    return {
        "comfyui": _comfyui_version(),
        "frontend": _dist_version("comfyui-frontend-package", "comfyui_frontend_package"),
        "python": platform.python_version(),
        "pytorch": torch_version or _dist_version("torch"),
        "cuda": cuda,
        "hip": hip,
        "cudnn": str(cudnn) if cudnn else None,
        "xformers": _dist_version("xformers"),
        "nvidia_driver": _nvidia_driver(),
        "os": _os_name(),
        "platform": platform.platform(),
        "arch": platform.machine(),
        "container": detect_container(),
    }


# ---- ComfyUI ----------------------------------------------------------------------------------------------------


def queue_state() -> dict[str, Any] | None:
    """Running and pending prompt counts from ComfyUI's queue, or None without a PromptServer (tests, CLI)."""
    server = sys.modules.get("server")  # Only ComfyUI's own server module; never import one.
    instance = getattr(getattr(server, "PromptServer", None), "instance", None)
    queue = getattr(instance, "prompt_queue", None)
    if queue is None:
        return None
    for name in ("get_current_queue_volatile", "get_current_queue"):  # Volatile skips a deep copy; newer ComfyUI.
        getter = getattr(queue, name, None)
        if getter is None:
            continue
        try:
            running, pending = getter()
            return {"running": len(running), "pending": len(pending), "remaining": len(running) + len(pending)}
        except Exception:
            continue
    try:
        remaining = int(queue.get_tasks_remaining())
    except Exception:
        return None
    return {"running": None, "pending": None, "remaining": remaining}


def comfyui_state() -> dict[str, Any]:
    now = time.time()
    return {
        "boot_id": BOOT_ID,
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "uptime_s": round(now - STARTED_AT, 1),
        "queue": queue_state(),
        "restart_mode": restart.restart_mode(),
    }


# ---- Everything -------------------------------------------------------------------------------------------------


def _section(name: str, notes: dict[str, str], fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception as exc:
        logger.debug("ComfyUI-SpookTools: %s stats failed", name, exc_info=True)
        notes[name] = f"{type(exc).__name__}: {exc}"
        return default


def collect(comfyui_base: Path | str) -> dict[str, Any]:
    """Snapshot of every section. Never raises; see the module docstring."""
    notes: dict[str, str] = {}
    host_memory = _attempt(lambda: psutil.virtual_memory().total) if psutil is not None else None
    limits = _attempt(cgroup_limits, host_memory) if sys.platform.startswith("linux") else None
    if psutil is None:
        notes["cpu"] = notes["ram"] = "psutil is not installed"
        cpu = ram = None
    else:
        cpu = _section("cpu", notes, lambda: cpu_stats(limits))
        ram = _section("ram", notes, lambda: ram_stats(limits))
    return {
        "time": time.time(),
        "cpu": cpu,
        "ram": ram,
        "gpus": _section("gpus", notes, lambda: gpu_stats(notes), default=[]),
        "disks": _section("disks", notes, lambda: disk_stats(Path(comfyui_base)), default=[]),
        "versions": _section("versions", notes, lambda: _cached("versions", _versions)),
        "comfyui": comfyui_state(),
        "notes": notes,
    }
