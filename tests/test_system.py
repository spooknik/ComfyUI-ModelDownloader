"""System feature: stats collection, the restart command builder, and the /system routes."""

import asyncio
import contextlib
import os
import sys
import types
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from spooktools import restart, routes_system, system_info
from spooktools.download_manager import DownloadManager
from spooktools.routes import API_PREFIX, LEGACY_API_PREFIX, register_routes

TOP_LEVEL_KEYS = {"time", "cpu", "ram", "gpus", "disks", "versions", "comfyui", "notes"}


@pytest.fixture(autouse=True)
def fresh_caches():
    system_info.reset_caches()
    yield
    system_info.reset_caches()


@pytest.fixture
def make_client(comfy: Path):
    @contextlib.asynccontextmanager
    async def factory():
        app = web.Application()
        register_routes(app, DownloadManager(comfyui_base=comfy))
        async with TestClient(TestServer(app)) as client:
            yield client

    return factory


@pytest.fixture
def restarts(monkeypatch):
    """Stub the real restart; records each call. Short delay so tests stay fast."""
    calls: list[float] = []
    monkeypatch.setattr(restart, "perform_restart", lambda: calls.append(asyncio.get_running_loop().time()))
    monkeypatch.setattr(routes_system, "RESTART_DELAY", 0.2)
    return calls


def fake_queue(monkeypatch, queue) -> None:
    """Install a fake ComfyUI `server` module whose PromptServer.instance has `queue` as its prompt_queue."""
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(app=None, prompt_queue=queue))
    monkeypatch.setitem(sys.modules, "server", server)


class VolatileQueue:
    def __init__(self, running: int, pending: int):
        self.running, self.pending = running, pending

    def get_current_queue_volatile(self):
        return [object()] * self.running, [object()] * self.pending


# ---- Restart command and mode -----------------------------------------------------------------------------------


def test_restart_command_plain_script():
    argv = ["./main.py", "--listen", "0.0.0.0", "--port", "8188", "--base-directory", "/basedir"]
    assert restart.build_restart_command(argv, "/opt/venv/bin/python3", "linux") == ["/opt/venv/bin/python3", *argv]
    # sys.orig_argv shows interpreter options sys.argv dropped: keep them.
    orig = ["python3", "-s", "-X", "utf8", *argv]
    assert restart.build_restart_command(argv, "/usr/bin/python3", "linux", orig) == [
        "/usr/bin/python3",
        "-s",
        "-X",
        "utf8",
        *argv,
    ]
    # An orig_argv that doesn't end with argv[1:] (something rewrote sys.argv) is ignored.
    assert restart.build_restart_command(argv, "/py", "linux", ["py", "-s", "other.py"]) == ["/py", *argv]


def test_restart_command_module_form():
    main = os.path.join("site-packages", "comfyui", "__main__.py")
    expected = ["/py", "-m", "comfyui", "--port", "1"]
    assert restart.build_restart_command([main, "--port", "1"], "/py", "linux") == expected
    orig = ["/py", "-s", "-m", "comfyui", "--port", "1"]
    assert restart.build_restart_command([main, "--port", "1"], "/py", "linux", orig) == [
        "/py",
        "-s",
        "-m",
        "comfyui",
        "--port",
        "1",
    ]


def test_restart_command_windows_standalone():
    argv = [r"C:\Comfy UI\main.py", "--windows-standalone-build", "--base-directory", r"D:\My Models", "--port", "8188"]
    orig = [r"C:\Comfy UI\python_embeded\python.exe", "-s", *argv]
    command = restart.build_restart_command(argv, r"C:\Comfy UI\python_embeded\python.exe", "win32", orig)
    assert command == [
        r'"C:\Comfy UI\python_embeded\python.exe"',
        "-s",
        r'"C:\Comfy UI\main.py"',
        "--base-directory",
        r'"D:\My Models"',
        "--port",
        "8188",
    ]
    # Not Windows: the flag is still dropped, nothing is quoted.
    command = restart.build_restart_command(["main.py", "--windows-standalone-build"], "/py", "linux")
    assert command == ["/py", "main.py"]


def test_restart_mode_selection():
    assert restart.restart_mode({}) == "exec"
    assert restart.restart_mode({"SPOOKTOOLS_RESTART_MODE": "Exit "}) == "exit"
    assert restart.restart_mode({"SPOOKTOOLS_RESTART_MODE": "bogus"}) == "exec"
    assert restart.restart_mode({"__COMFY_CLI_SESSION__": "/tmp/s", "SPOOKTOOLS_RESTART_MODE": "exit"}) == "comfy-cli"


@pytest.fixture
def process_stubs(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(restart, "exec_process", lambda exe, cmd: calls.append(("exec", exe, cmd)))
    monkeypatch.setattr(restart, "exit_process", lambda code=0: calls.append(("exit", code)))
    return calls


def test_perform_restart_exec(monkeypatch, process_stubs):
    monkeypatch.delenv("__COMFY_CLI_SESSION__", raising=False)
    monkeypatch.delenv("SPOOKTOOLS_RESTART_MODE", raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "--windows-standalone-build", "--port", "8188"])
    monkeypatch.setattr(sys, "orig_argv", ["python", "main.py", "--windows-standalone-build", "--port", "8188"])
    restart.perform_restart()
    expected = restart.build_restart_command(["main.py", "--port", "8188"], sys.executable, sys.platform)
    assert process_stubs == [("exec", sys.executable, expected)]


def test_perform_restart_comfy_cli_and_exit(tmp_path: Path, monkeypatch, process_stubs):
    session = tmp_path / "session"
    monkeypatch.setenv("__COMFY_CLI_SESSION__", str(session))
    restart.perform_restart()
    assert (tmp_path / "session.reboot").exists()
    assert process_stubs == [("exit", 0)]

    monkeypatch.delenv("__COMFY_CLI_SESSION__")
    monkeypatch.setenv("SPOOKTOOLS_RESTART_MODE", "exit")
    restart.perform_restart()
    assert process_stubs == [("exit", 0), ("exit", 0)]


# ---- Stats ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", [API_PREFIX, LEGACY_API_PREFIX])
def test_stats_shape(make_client, comfy: Path, prefix: str):
    async def scenario():
        async with make_client() as client:
            resp = await client.get(f"{prefix}/system/stats")
            assert resp.status == 200
            return await resp.json()

    data = asyncio.run(scenario())
    assert set(data) == TOP_LEVEL_KEYS
    assert data["cpu"]["logical"] >= 1 and isinstance(data["cpu"]["per_core"], list)
    assert 0 <= data["cpu"]["percent"] <= 100
    assert data["ram"]["total"] > 0 and data["ram"]["process_rss"] > 0
    assert isinstance(data["gpus"], list)
    # No folder_paths outside ComfyUI: every role falls back to <base>/<role>, all on one filesystem.
    assert len(data["disks"]) == 1 and data["disks"][0]["label"] == "models, output, input, temp"
    assert data["disks"][0]["paths"]["models"] == str(comfy / "models") and data["disks"][0]["free"] > 0
    assert data["versions"]["python"] == ".".join(map(str, sys.version_info[:3]))
    assert data["comfyui"]["boot_id"] == system_info.BOOT_ID and data["comfyui"]["queue"] is None
    assert data["comfyui"]["pid"] == os.getpid() and data["comfyui"]["restart_mode"] in ("exec", "exit", "comfy-cli")


def test_stats_degrade_without_optional_libraries(make_client, monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)  # `import pynvml` raises ImportError
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(system_info, "psutil", None)

    async def scenario():
        async with make_client() as client:
            resp = await client.get(f"{API_PREFIX}/system/stats")
            assert resp.status == 200
            return await resp.json()

    data = asyncio.run(scenario())
    assert set(data) == TOP_LEVEL_KEYS
    assert data["cpu"] is None and data["ram"] is None and data["gpus"] == []
    assert "psutil" in data["notes"]["cpu"] and "nvidia-ml-py" in data["notes"]["gpus"]
    assert data["versions"]["cuda"] is None and data["versions"]["cudnn"] is None
    assert data["disks"] and data["comfyui"]["boot_id"] == system_info.BOOT_ID


def make_fake_nvml(uuids: list[str]) -> types.SimpleNamespace:
    def not_supported(handle):
        raise RuntimeError("NVML_ERROR_NOT_SUPPORTED")

    return types.SimpleNamespace(
        NVML_TEMPERATURE_GPU=0,
        nvmlInit=lambda: None,
        nvmlSystemGetDriverVersion=lambda: b"580.65",
        nvmlDeviceGetCount=lambda: len(uuids),
        nvmlDeviceGetHandleByIndex=lambda i: i,
        nvmlDeviceGetName=lambda h: f"GPU {h}".encode(),
        nvmlDeviceGetUUID=lambda h: uuids[h],
        nvmlDeviceGetMemoryInfo=lambda h: types.SimpleNamespace(used=(h + 1) << 30, total=16 << 30),
        nvmlDeviceGetUtilizationRates=lambda h: types.SimpleNamespace(gpu=40 + h),
        nvmlDeviceGetTemperature=lambda h, sensor: 60,
        nvmlDeviceGetPowerUsage=lambda h: 123_456,
        nvmlDeviceGetEnforcedPowerLimit=lambda h: 300_000,
        nvmlDeviceGetFanSpeed=not_supported,
    )


def make_fake_torch(uuids: list[str], reserved: list[int], initialized: bool = True):
    mem_get_info_calls: list[int] = []

    def mem_get_info(i):
        mem_get_info_calls.append(i)
        return (4 << 30, 16 << 30)

    cuda = types.SimpleNamespace(
        is_initialized=lambda: initialized,
        device_count=lambda: len(uuids),
        get_device_properties=lambda i: types.SimpleNamespace(name=f"torch {i}", uuid=uuids[i], total_memory=16 << 30),
        memory_reserved=lambda i: reserved[i],
        memory_allocated=lambda i: reserved[i] // 2,
        mem_get_info=mem_get_info,
    )
    torch = types.SimpleNamespace(
        __version__="2.9.0+cu128",
        cuda=cuda,
        version=types.SimpleNamespace(cuda="12.8", hip=None),
        backends=types.SimpleNamespace(cudnn=types.SimpleNamespace(version=lambda: 91002)),
    )
    return torch, mem_get_info_calls


def test_gpu_stats_merge_nvml_and_torch(monkeypatch):
    # NVML lists GPUs in PCI order; PyTorch (CUDA_VISIBLE_DEVICES=1,0 say) the other way round.
    monkeypatch.setitem(sys.modules, "pynvml", make_fake_nvml(["GPU-aaaa", "GPU-bbbb"]))
    torch, mem_calls = make_fake_torch(["bbbb", "aaaa"], reserved=[6 << 30, 0])
    monkeypatch.setitem(sys.modules, "torch", torch)

    data = system_info.collect(".")
    gpus = data["gpus"]
    assert [g["name"] for g in gpus] == ["GPU 0", "GPU 1"] and all(g["source"] == "nvml" for g in gpus)
    assert gpus[0]["util_percent"] == 40 and gpus[0]["power_w"] == 123.5 and gpus[0]["power_limit_w"] == 300.0
    assert gpus[0]["fan_percent"] is None and gpus[0]["temperature_c"] == 60
    assert gpus[1]["torch"]["index"] == 0 and gpus[1]["torch"]["reserved"] == 6 << 30
    assert gpus[1]["torch"]["free"] == 4 << 30
    assert gpus[0]["torch"]["index"] == 1 and gpus[0]["torch"]["free"] is None
    assert mem_calls == [0]  # Never mem_get_info a device PyTorch hasn't touched (it would create a CUDA context).
    versions = data["versions"]
    assert versions["nvidia_driver"] == "580.65" and versions["pytorch"] == "2.9.0+cu128"
    assert versions["cuda"] == "12.8" and versions["cudnn"] == "91002"
    assert "gpus" not in data["notes"]


def test_gpu_stats_torch_only_and_uninitialised(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)
    torch, mem_calls = make_fake_torch(["cccc"], reserved=[1 << 30])
    monkeypatch.setitem(sys.modules, "torch", torch)
    gpus = system_info.collect(".")["gpus"]
    assert len(gpus) == 1 and gpus[0]["source"] == "torch" and gpus[0]["name"] == "torch 0"
    assert gpus[0]["mem_used"] == 12 << 30 and gpus[0]["mem_total"] == 16 << 30 and gpus[0]["util_percent"] is None

    torch, mem_calls = make_fake_torch(["cccc"], reserved=[0], initialized=False)
    monkeypatch.setitem(sys.modules, "torch", torch)
    data = system_info.collect(".")
    assert data["gpus"] == [] and mem_calls == [] and "CUDA is not initialised" in data["notes"]["gpus"]


def test_disks_use_folder_paths_and_group_by_filesystem(tmp_path: Path, monkeypatch):
    (tmp_path / "basedir" / "models").mkdir(parents=True)
    (tmp_path / "basedir" / "output").mkdir()
    fake_fp = types.SimpleNamespace(
        models_dir=str(tmp_path / "basedir" / "models"),
        get_output_directory=lambda: str(tmp_path / "basedir" / "output"),
        get_input_directory=lambda: str(tmp_path / "basedir" / "input"),  # missing: parent's filesystem
        get_temp_directory=lambda: str(tmp_path / "basedir" / "temp"),
    )
    monkeypatch.setitem(sys.modules, "folder_paths", fake_fp)
    disks = system_info.disk_stats(tmp_path / "elsewhere")
    assert len(disks) == 1 and disks[0]["roles"] == ["models", "output", "input", "temp"]
    assert disks[0]["paths"]["output"] == str(tmp_path / "basedir" / "output")


def test_cpu_sampler_is_shared_across_threads(monkeypatch):
    """psutil >= 6 keeps cpu_percent's last sample per thread; ours is shared, so any executor thread gets a real %."""
    import collections
    import threading

    Times = collections.namedtuple("Times", "user system idle")
    samples = iter(
        [
            (Times(10, 0, 90), [Times(5, 0, 45), Times(5, 0, 45)]),
            (Times(30, 0, 170), [Times(20, 0, 80), Times(15, 0, 95)]),
        ]
    )
    current: dict = {}

    def cpu_times(percpu=False):
        if not percpu:
            current["now"] = next(samples)
        return current["now"][1] if percpu else current["now"][0]

    monkeypatch.setattr(system_info, "psutil", types.SimpleNamespace(cpu_times=cpu_times))
    sampler = system_info._CpuSampler()
    assert sampler.sample() == (None, [])  # First sample: nothing to compare with yet.
    result: list = []
    thread = threading.Thread(target=lambda: result.append(sampler.sample()))
    thread.start()
    thread.join()
    # Overall: busy 20 of 100 ticks. Core 0: 15 of 50; core 1: 10 of 60.
    assert result == [(20.0, [30.0, 16.7])]
    assert sampler.sample() == (20.0, [30.0, 16.7])  # Within MIN_INTERVAL: the same answer, no new sample.


def test_cgroup_limits(tmp_path: Path):
    v2 = tmp_path / "v2"
    v2.mkdir()
    (v2 / "cgroup.controllers").write_text("cpu memory")
    (v2 / "memory.max").write_text("2147483648\n")
    (v2 / "memory.current").write_text("1000000\n")
    (v2 / "memory.stat").write_text("anon 5\ninactive_file 200000\nactive_file 1\n")
    (v2 / "cpu.max").write_text("200000 100000\n")
    proc = tmp_path / "cgroup"
    proc.write_text("0::/\n")
    limits = system_info.cgroup_limits(64 << 30, v2, proc)
    assert limits == {
        "version": 2,
        "memory_limit": 2147483648,
        "memory_used": 800000,
        "memory_percent": round(100 * 800000 / 2147483648, 1),
        "cpu_limit": 2.0,
    }
    (v2 / "memory.max").write_text("max\n")
    (v2 / "cpu.max").write_text("max 100000\n")
    assert system_info.cgroup_limits(64 << 30, v2, proc) is None

    v1 = tmp_path / "v1"
    (v1 / "memory").mkdir(parents=True)
    (v1 / "cpu,cpuacct").mkdir()
    (v1 / "memory" / "memory.limit_in_bytes").write_text("9223372036854771712")  # "unlimited"
    (v1 / "memory" / "memory.usage_in_bytes").write_text("5000")
    (v1 / "cpu,cpuacct" / "cpu.cfs_quota_us").write_text("150000")
    (v1 / "cpu,cpuacct" / "cpu.cfs_period_us").write_text("100000")
    limits = system_info.cgroup_limits(64 << 30, v1, proc)
    assert limits["version"] == 1 and limits["memory_limit"] is None and limits["cpu_limit"] == 1.5

    assert system_info.cgroup_limits(64 << 30, tmp_path / "missing", proc) is None


def test_queue_state_api_variants(monkeypatch):
    fake_queue(monkeypatch, VolatileQueue(1, 2))
    assert system_info.queue_state() == {"running": 1, "pending": 2, "remaining": 3}

    class OldQueue:  # Older ComfyUI: no volatile getter.
        def get_current_queue(self):
            return [], [object()]

    fake_queue(monkeypatch, OldQueue())
    assert system_info.queue_state() == {"running": 0, "pending": 1, "remaining": 1}

    fake_queue(monkeypatch, types.SimpleNamespace(get_tasks_remaining=lambda: 4))
    assert system_info.queue_state() == {"running": None, "pending": None, "remaining": 4}

    fake_queue(monkeypatch, None)
    assert system_info.queue_state() is None


# ---- Restart route ----------------------------------------------------------------------------------------------


def test_restart_refuses_when_busy(make_client, monkeypatch, restarts):
    fake_queue(monkeypatch, VolatileQueue(1, 2))

    async def scenario():
        async with make_client() as client:
            resp = await client.post(f"{API_PREFIX}/system/restart", json={"force": False})
            assert resp.status == 409
            body = await resp.json()
            assert body["running"] == 1 and body["pending"] == 2 and body["remaining"] == 3
            resp = await client.post(f"{API_PREFIX}/system/restart")  # No body = not forced.
            assert resp.status == 409
            resp = await client.post(f"{API_PREFIX}/system/restart", json={"force": "yes"})  # Only a real true.
            assert resp.status == 409
            await asyncio.sleep(0.4)

    asyncio.run(scenario())
    assert restarts == []


@pytest.mark.parametrize("prefix", [API_PREFIX, LEGACY_API_PREFIX])
@pytest.mark.parametrize("busy", [False, True])
def test_restart_schedules_after_response(make_client, monkeypatch, restarts, prefix: str, busy: bool):
    fake_queue(monkeypatch, VolatileQueue(1 if busy else 0, 0))

    async def scenario():
        async with make_client() as client:
            sent = asyncio.get_running_loop().time()
            resp = await client.post(f"{prefix}/system/restart", json={"force": busy})
            assert resp.status == 202
            body = await resp.json()
            assert body["status"] == "restarting" and body["boot_id"] == system_info.BOOT_ID
            assert restarts == []  # Scheduled, not run inline: the response must get out first.
            # A second click while one is pending doesn't schedule another restart.
            resp = await client.post(f"{prefix}/system/restart", json={"force": True})
            assert resp.status == 202
            await asyncio.sleep(0.5)
            return sent

    sent = asyncio.run(scenario())
    assert len(restarts) == 1 and restarts[0] - sent >= 0.19


def test_restart_rejects_form_posts_and_bad_json(make_client, restarts):
    async def scenario():
        async with make_client() as client:
            for content_type in ("text/plain", "application/x-www-form-urlencoded"):
                resp = await client.post(
                    f"{API_PREFIX}/system/restart", data=b'{"force": true}', headers={"Content-Type": content_type}
                )
                assert resp.status == 415
            resp = await client.post(
                f"{API_PREFIX}/system/restart", data=b"nope", headers={"Content-Type": "application/json"}
            )
            assert resp.status == 400
            resp = await client.post(f"{API_PREFIX}/system/restart", json=[1])
            assert resp.status == 400
            await asyncio.sleep(0.3)

    asyncio.run(scenario())
    assert restarts == []


def test_failed_restart_can_be_retried(make_client, monkeypatch):
    attempts: list[int] = []

    def failing_restart():
        attempts.append(1)
        raise OSError("exec failed")

    monkeypatch.setattr(restart, "perform_restart", failing_restart)
    monkeypatch.setattr(routes_system, "RESTART_DELAY", 0.05)

    async def scenario():
        async with make_client() as client:
            for _ in range(2):
                resp = await client.post(f"{API_PREFIX}/system/restart", json={})
                assert resp.status == 202
                await asyncio.sleep(0.2)

    asyncio.run(scenario())
    assert len(attempts) == 2


def test_restart_refuses_cross_site_requests(make_client, monkeypatch, restarts):
    """What a hostile page can send from a visitor's browser without a CORS preflight must never restart ComfyUI."""
    fake_queue(monkeypatch, VolatileQueue(0, 0))

    async def scenario():
        async with make_client() as client:
            # fetch(url, {method: "POST", mode: "no-cors"}): no body, so no Content-Type at all.
            resp = await client.post(f"{API_PREFIX}/system/restart", skip_auto_headers=["Content-Type"])
            assert resp.status == 415
            # Modern browsers label it; refused even with an otherwise acceptable content type.
            resp = await client.post(
                f"{API_PREFIX}/system/restart", json={"force": True}, headers={"Sec-Fetch-Site": "cross-site"}
            )
            assert resp.status == 403
            await asyncio.sleep(0.4)
            assert restarts == []
            # Same-origin requests from our own frontend still work.
            resp = await client.post(
                f"{API_PREFIX}/system/restart", json={"force": True}, headers={"Sec-Fetch-Site": "same-origin"}
            )
            assert resp.status == 202
            await asyncio.sleep(0.4)
            assert len(restarts) == 1

    asyncio.run(scenario())
