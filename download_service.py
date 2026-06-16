"""Standalone aiohttp service for downloading models into ComfyUI folders."""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import functools
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

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
    eta_seconds: float = 0.0
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


class DownloadService:
    def __init__(
        self,
        comfyui_base: Path | str,
        port: int = 8189,
        host: str = "127.0.0.1",
        extra_folders: list[str] | None = None,
        chunk_size: int = 8192,
    ) -> None:
        self.comfyui_base = Path(comfyui_base).resolve()
        self.port = port
        self.host = host
        self.chunk_size = chunk_size
        self.common_folders: list[str] = list(extra_folders or []) + list(DEFAULT_COMMON_FOLDERS)
        self._downloads: dict[str, DownloadEntry] = {}
        self._lock = asyncio.Lock()
        self._app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/folders", self.handle_folders)
        self._app.router.add_post("/download", self.handle_download)
        self._app.router.add_get("/downloads", self.handle_downloads)
        self._app.router.add_get("/progress/{download_id}", self.handle_progress)
        self._app.router.add_delete("/download/{download_id}", self.handle_cancel)
        self._app.router.add_options("/{tail:.*}", self.handle_options)

    async def handle_options(self, request: web.Request) -> web.Response:
        return web.Response(status=204, headers=self._cors_headers())

    def _cors_headers(self) -> dict[str, str]:
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

    def _json_response(self, data: Any, status: int = 200) -> web.Response:
        return web.json_response(data, status=status, headers=self._cors_headers())

    def _resolve_folder(self, folder_name: str) -> Path:
        candidate = (self.comfyui_base / "models" / folder_name).resolve()
        if not str(candidate).startswith(str(self.comfyui_base)):
            raise ValueError("Invalid folder path")
        return candidate

    async def handle_folders(self, request: web.Request) -> web.Response:
        folders: list[dict[str, Any]] = []
        for name in self.common_folders:
            path = self._resolve_folder(name)
            folders.append({"name": name, "path": str(path), "exists": path.exists()})
        return self._json_response({"base": str(self.comfyui_base), "folders": folders})

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        # Remove any path separators and unsafe chars.
        name = re.sub(r'[\\/:*?"<>|]', "_", name)
        name = name.strip("._")
        if not name:
            name = "download.bin"
        return name

    async def handle_download(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._json_response({"error": "Invalid JSON"}, status=400)

        url = str(payload.get("url", "")).strip()
        folder_name = str(payload.get("folder", "")).strip()
        custom_filename = str(payload.get("filename", "")).strip()

        if not url:
            return self._json_response({"error": "Missing URL"}, status=400)
        if not folder_name:
            return self._json_response({"error": "Missing folder"}, status=400)
        if not url.startswith(("http://", "https://")):
            return self._json_response({"error": "URL must be http or https"}, status=400)

        try:
            folder_path = self._resolve_folder(folder_name)
        except ValueError as exc:
            return self._json_response({"error": f"Invalid folder: {exc}"}, status=400)

        if not folder_path.exists():
            folder_path.mkdir(parents=True, exist_ok=True)

        # Determine filename.
        if custom_filename:
            filename = self._sanitize_filename(custom_filename)
        else:
            filename = self._infer_filename_from_url(url)

        destination = folder_path / filename
        if destination.exists() and not payload.get("overwrite"):
            return self._json_response(
                {"error": "File already exists. Set overwrite=true to replace."}, status=409
            )

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
        return self._json_response(entry.to_dict(), status=202)

    @staticmethod
    def _infer_filename_from_url(url: str) -> str:
        from urllib.parse import unquote, urlparse

        parsed = urlparse(url)
        basename = Path(unquote(parsed.path)).name
        basename = DownloadService._sanitize_filename(basename)
        if not basename or basename == "download.bin":
            basename = "model.bin"
        return basename

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
                    entry.bytes_total = response.headers.get("Content-Length", None) or 0
                    if entry.bytes_total:
                        entry.bytes_total = int(entry.bytes_total)

                    # Stream to a temporary file using a thread for disk writes.
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
                        # Move temp file into place.
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

    async def handle_downloads(self, request: web.Request) -> web.Response:
        status_filter = request.query.get("status")
        async with self._lock:
            downloads = list(self._downloads.values())
        if status_filter:
            downloads = [d for d in downloads if d.status == status_filter]
        return self._json_response([d.to_dict() for d in downloads])

    async def handle_progress(self, request: web.Request) -> web.Response:
        download_id = request.match_info["download_id"]
        entry = self._downloads.get(download_id)
        if not entry:
            return self._json_response({"error": "Download not found"}, status=404)
        return self._json_response(entry.to_dict())

    async def handle_cancel(self, request: web.Request) -> web.Response:
        download_id = request.match_info["download_id"]
        entry = self._downloads.get(download_id)
        if not entry:
            return self._json_response({"error": "Download not found"}, status=404)
        if entry.status not in ("pending", "running"):
            return self._json_response({"error": f"Cannot cancel download in status {entry.status}"}, status=409)
        entry._cancel_event.set()
        if entry.task:
            entry.task.cancel()
        return self._json_response({"status": "cancellation requested", "download": entry.to_dict()})

    async def start(self) -> None:
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info("Model download service running at http://%s:%d", self.host, self.port)

    def start_in_thread(self) -> threading.Thread:
        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.start())
            # Keep loop alive with a no-op task.
            async def keep_alive():
                while True:
                    await asyncio.sleep(3600)
            loop.run_until_complete(keep_alive())

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    async def start_blocking(self) -> None:
        await self.start()
        while True:
            await asyncio.sleep(3600)


def create_default_service() -> DownloadService:
    """Create a service with sane defaults inferred from the environment."""
    comfy_path: Path | None = None
    if os.environ.get("COMFYUI_PATH"):
        comfy_path = Path(os.environ["COMFYUI_PATH"]).resolve()
    else:
        # Try to infer from typical custom_nodes location: custom_nodes/<this>/../..
        candidate = Path(__file__).resolve().parent.parent.parent
        if (candidate / "main.py").exists() or (candidate / "comfy").exists():
            comfy_path = candidate
    if not comfy_path:
        comfy_path = Path.cwd()

    port = int(os.environ.get("COMFY_MODEL_DL_PORT", "8189"))
    host = os.environ.get("COMFY_MODEL_DL_HOST", "127.0.0.1")
    return DownloadService(comfyui_base=comfy_path, port=port, host=host)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = create_default_service()
    try:
        asyncio.run(service.start_blocking())
    except KeyboardInterrupt:
        logger.info("Shutting down")
