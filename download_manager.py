"""Core model-download manager (server-agnostic)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import AsyncIterable
from pathlib import Path, PurePosixPath
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_COMMON_FOLDERS = [
    "checkpoints",
    "loras",
    "vae",
    "controlnet",
    "clip",
    "clip_vision",
    "unet",
    "diffusion_models",
    "upscale_models",
    "embeddings",
    "inpaint",
    "ipadapter",
    "instantid",
]

TEMP_SUFFIX = ".tmp"


def error_status(error: str | None) -> int:
    """Map a manager error message to an HTTP status code."""
    msg = (error or "").lower()
    if "not found" in msg:
        return 404
    if "already exists" in msg or "in progress" in msg or "cannot cancel" in msg:
        return 409
    if "disk space" in msg:
        return 507
    return 400


@dataclasses.dataclass(slots=True)
class DownloadEntry:
    download_id: str
    url: str
    folder: str
    destination: Path
    status: str  # "pending" | "running" | "completed" | "failed" | "cancelled"
    bytes_downloaded: int = 0
    bytes_total: int = 0
    speed_bps: float = 0.0
    eta_seconds: float | None = None
    error: str | None = None
    created_at: float = dataclasses.field(default_factory=time.time)
    completed_at: float | None = None
    task: asyncio.Task | None = None
    _cancel_event: threading.Event = dataclasses.field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "download_id": self.download_id,
            "url": self.url,
            "folder": self.folder,
            "destination": str(self.destination),
            "filename": self.destination.name,
            "status": self.status,
            "bytes_downloaded": self.bytes_downloaded,
            "bytes_total": self.bytes_total,
            "speed_bps": round(self.speed_bps, 2),
            "eta_seconds": round(self.eta_seconds, 2) if self.eta_seconds else None,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class DownloadManager:
    """Handles download state, validation, and async worker execution.

    This class has no HTTP dependencies and can be wired to any ASGI/aiohttp/FastAPI server.
    """

    def __init__(
        self,
        comfyui_base: Path | str,
        extra_folders: list[str] | None = None,
        chunk_size: int = 8192,
    ) -> None:
        self.comfyui_base = Path(comfyui_base).resolve()
        self.chunk_size = chunk_size
        self.common_folders: list[str] = list(extra_folders or []) + list(DEFAULT_COMMON_FOLDERS)
        self._downloads: dict[str, DownloadEntry] = {}
        self._uploads: set[Path] = set()
        self._lock = asyncio.Lock()

    def _resolve_folder(self, folder_name: str) -> Path:
        # Folder names are a single path component directly under models/. Checked lexically (not via
        # resolve()) so symlinked model folders keep working while ".." and separators are rejected.
        if not folder_name or folder_name in (".", "..") or re.search(r'[\\/:*?"<>|]', folder_name):
            raise ValueError("Invalid folder path")
        return self.comfyui_base / "models" / folder_name

    def _resolve_file(self, folder_name: str, relative_path: str) -> Path:
        rel = PurePosixPath(relative_path.replace("\\", "/"))
        if not rel.parts or rel.is_absolute() or ".." in rel.parts or ":" in relative_path:
            raise ValueError("Invalid file path")
        return self._resolve_folder(folder_name).joinpath(*rel.parts)

    def _active_paths(self) -> set[Path]:
        """Destinations (and their temp files) of in-flight downloads and uploads."""
        paths = set(self._uploads)
        paths.update(d.destination for d in self._downloads.values() if d.status in ("pending", "running"))
        return paths | {p.with_name(p.name + TEMP_SUFFIX) for p in paths}

    def list_folders(self) -> dict[str, Any]:
        models_root = self.comfyui_base / "models"
        # Discover all existing subdirectories under models/.
        discovered: set[str] = set()
        if models_root.exists():
            for child in models_root.iterdir():
                if child.is_dir():
                    discovered.add(child.name)

        # Merge with the default/common list, preserving default order first.
        folder_names = list(dict.fromkeys(self.common_folders + sorted(discovered)))

        folders: list[dict[str, Any]] = []
        for name in folder_names:
            path = self._resolve_folder(name)
            folders.append({"name": name, "path": str(path), "exists": path.exists()})
        return {"base": str(self.comfyui_base), "folders": folders}

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        name = re.sub(r'[\\/:*?"<>|]', "_", name)
        name = name.strip("._")
        if not name:
            name = "download.bin"
        return name

    @staticmethod
    def _infer_filename_from_url(url: str) -> str:
        from urllib.parse import unquote, urlparse

        parsed = urlparse(url)
        basename = Path(unquote(parsed.path)).name
        basename = DownloadManager._sanitize_filename(basename)
        if not basename or basename == "download.bin":
            basename = "model.bin"
        return basename

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Convert known non-direct URLs to direct download URLs."""
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        # Hugging Face blob pages -> resolve raw file URL.
        if parsed.netloc.lower() in ("huggingface.co", "www.huggingface.co"):
            match = re.match(
                r"^/([^/]+/[^/]+)/blob/(.*)$",
                parsed.path,
            )
            if match:
                repo, path_in_repo = match.groups()
                direct_path = f"/{repo}/resolve/{path_in_repo}"
                return urlunparse(parsed._replace(path=direct_path))
        return url

    async def start_download(
        self,
        url: str,
        folder_name: str,
        custom_filename: str | None = None,
        overwrite: bool = False,
    ) -> tuple[DownloadEntry, bool, str | None]:
        """Validate inputs, create a DownloadEntry, and start the worker.

        Returns (entry, success, error_message_or_none).
        """
        url = url.strip()
        url = self._normalize_url(url)
        folder_name = folder_name.strip()
        custom_filename = (custom_filename or "").strip()

        if not url:
            return None, False, "Missing URL"  # type: ignore[return-value]
        if not folder_name:
            return None, False, "Missing folder"  # type: ignore[return-value]
        if not url.startswith(("http://", "https://")):
            return None, False, "URL must be http or https"  # type: ignore[return-value]

        try:
            folder_path = self._resolve_folder(folder_name)
        except ValueError as exc:
            return None, False, f"Invalid folder: {exc}"  # type: ignore[return-value]

        if not folder_path.exists():
            folder_path.mkdir(parents=True, exist_ok=True)

        filename = self._sanitize_filename(custom_filename) if custom_filename else self._infer_filename_from_url(url)
        destination = folder_path / filename

        if destination in self._active_paths():
            return None, False, "A transfer to this file is already in progress"  # type: ignore[return-value]
        if destination.exists() and not overwrite:
            return None, False, "File already exists. Set overwrite=true to replace."  # type: ignore[return-value]

        download_id = str(uuid.uuid4())
        entry = DownloadEntry(
            download_id=download_id,
            url=url,
            folder=folder_name,
            destination=destination,
            status="pending",
        )
        async with self._lock:
            self._downloads[download_id] = entry

        entry.task = asyncio.create_task(self._download_worker(entry))
        return entry, True, None

    async def _download_worker(self, entry: DownloadEntry) -> None:
        try:
            entry.status = "running"
            temp_destination = entry.destination.with_suffix(entry.destination.suffix + ".tmp")

            loop = asyncio.get_running_loop()
            timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(entry.url) as response:
                    if response.status >= 400:
                        raise RuntimeError(f"HTTP {response.status}: {response.reason}")

                    content_type = (response.headers.get("Content-Type", "") or "").lower()
                    # Reject HTML pages (blob pages, login walls, etc.) before writing anything.
                    if "text/html" in content_type:
                        raise RuntimeError(
                            "Server returned an HTML page instead of a binary file. "
                            "Please use the direct file URL (e.g. Hugging Face /resolve/...)."
                        )

                    content_length = response.headers.get("Content-Length")
                    entry.bytes_total = int(content_length) if content_length else 0

                    def write_chunks():
                        bytes_received = 0
                        start_time = time.monotonic()
                        with open(temp_destination, "wb") as f:
                            while True:
                                if entry._cancel_event.is_set():
                                    return False
                                chunk = asyncio.run_coroutine_threadsafe(
                                    response.content.read(self.chunk_size), loop
                                ).result(timeout=90)
                                if not chunk:
                                    break
                                f.write(chunk)
                                bytes_received += len(chunk)
                                elapsed = time.monotonic() - start_time
                                entry.bytes_downloaded = bytes_received
                                entry.speed_bps = bytes_received / elapsed if elapsed > 0 else 0.0
                                if entry.bytes_total and entry.speed_bps > 0:
                                    remaining = entry.bytes_total - bytes_received
                                    entry.eta_seconds = remaining / entry.speed_bps
                        return True

                    success = await loop.run_in_executor(None, write_chunks)
                    if not success:
                        entry.status = "cancelled"
                    else:
                        await loop.run_in_executor(None, self._atomic_move, temp_destination, entry.destination)
                        entry.status = "completed"
                        entry.completed_at = time.time()
        except concurrent.futures.TimeoutError as exc:
            entry.status = "failed"
            entry.error = f"Download timed out: {exc}"
        except Exception as exc:
            logger.exception("Download failed")
            entry.status = "failed"
            entry.error = str(exc)
        finally:
            temp_destination = entry.destination.with_suffix(entry.destination.suffix + ".tmp")
            if temp_destination.exists() and entry.status in ("failed", "cancelled"):
                try:
                    await asyncio.get_running_loop().run_in_executor(None, temp_destination.unlink)
                except Exception:
                    pass

    @staticmethod
    def _atomic_move(src: Path, dst: Path) -> None:
        if dst.exists():
            dst.unlink()
        shutil.move(str(src), str(dst))

    async def list_downloads(self, status_filter: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            downloads = list(self._downloads.values())
        if status_filter:
            downloads = [d for d in downloads if d.status == status_filter]
        return [d.to_dict() for d in downloads]

    def get_download(self, download_id: str) -> DownloadEntry | None:
        return self._downloads.get(download_id)

    def cancel_download(self, download_id: str) -> tuple[bool, str | None]:
        entry = self._downloads.get(download_id)
        if not entry:
            return False, "Download not found"
        if entry.status not in ("pending", "running"):
            return False, f"Cannot cancel download in status {entry.status}"
        entry._cancel_event.set()
        if entry.task:
            entry.task.cancel()
        return True, None

    def list_files(self, folder_name: str) -> tuple[dict[str, Any] | None, str | None]:
        """List files (recursively) inside a model folder. Blocking; run in an executor."""
        try:
            folder = self._resolve_folder(folder_name)
        except ValueError as exc:
            return None, f"Invalid folder: {exc}"

        active = self._active_paths()
        files: list[dict[str, Any]] = []
        seen_dirs: set[str] = set()
        for root, dirs, names in os.walk(folder, followlinks=True):
            real = os.path.realpath(root)
            if real in seen_dirs:  # Symlink cycle.
                dirs[:] = []
                continue
            seen_dirs.add(real)
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in names:
                if name.startswith("."):
                    continue
                path = Path(root) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append(
                    {
                        "path": path.relative_to(folder).as_posix(),
                        "name": name,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "active": path in active,
                    }
                )
        files.sort(key=lambda f: f["path"].lower())
        return {
            "folder": folder_name,
            "path": str(folder),
            "files": files,
            "total_size": sum(f["size"] for f in files),
        }, None

    def delete_file(self, folder_name: str, relative_path: str) -> tuple[bool, str | None]:
        try:
            path = self._resolve_file(folder_name, relative_path)
        except ValueError as exc:
            return False, str(exc)
        if path in self._active_paths():
            return False, "Cannot delete a file while a transfer is in progress"
        if not path.is_file() and not path.is_symlink():
            return False, "File not found"
        try:
            path.unlink()
        except OSError as exc:
            return False, f"Delete failed: {exc}"
        logger.info("Deleted model file %s", path)
        return True, None

    async def save_upload(
        self,
        folder_name: str,
        filename: str,
        chunks: AsyncIterable[bytes],
        expected_size: int | None = None,
        overwrite: bool = False,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Stream an uploaded file into a model folder via a temp file, then move it into place."""
        folder_name = (folder_name or "").strip()
        filename = (filename or "").strip()
        if not folder_name:
            return None, "Missing folder"
        if not filename:
            return None, "Missing filename"
        try:
            folder_path = self._resolve_folder(folder_name)
        except ValueError as exc:
            return None, f"Invalid folder: {exc}"

        destination = folder_path / self._sanitize_filename(filename)
        if destination in self._active_paths():
            return None, "A transfer to this file is already in progress"
        if destination.exists() and not overwrite:
            return None, "File already exists. Set overwrite=true to replace."

        folder_path.mkdir(parents=True, exist_ok=True)
        if expected_size:
            free = shutil.disk_usage(folder_path).free
            if expected_size > free:
                return None, f"Not enough disk space ({expected_size} bytes needed, {free} free)"

        temp_destination = destination.with_name(destination.name + TEMP_SUFFIX)
        loop = asyncio.get_running_loop()
        self._uploads.add(destination)
        received = 0
        ok = False
        try:
            f = await loop.run_in_executor(None, open, temp_destination, "wb")
            try:
                async for chunk in chunks:
                    await loop.run_in_executor(None, f.write, chunk)
                    received += len(chunk)
            finally:
                await loop.run_in_executor(None, f.close)
            if expected_size is not None and received != expected_size:
                return None, f"Upload incomplete: received {received} of {expected_size} bytes"
            await loop.run_in_executor(None, self._atomic_move, temp_destination, destination)
            ok = True
        except Exception as exc:
            logger.warning("Upload of %s failed: %s", destination, exc)
            return None, f"Upload failed: {exc}"
        finally:
            self._uploads.discard(destination)
            if not ok:
                temp_destination.unlink(missing_ok=True)

        logger.info("Uploaded model file %s (%d bytes)", destination, received)
        return {
            "folder": folder_name,
            "filename": destination.name,
            "destination": str(destination),
            "size": received,
        }, None


def create_default_manager() -> DownloadManager:
    """Create a manager with sane defaults inferred from the environment."""
    comfy_path: Path | None = None
    if os.environ.get("COMFYUI_PATH"):
        comfy_path = Path(os.environ["COMFYUI_PATH"]).resolve()
    else:
        candidate = Path(__file__).resolve().parent.parent.parent
        if (candidate / "main.py").exists() or (candidate / "comfy").exists():
            comfy_path = candidate
    if not comfy_path:
        comfy_path = Path.cwd()
    return DownloadManager(comfyui_base=comfy_path)
