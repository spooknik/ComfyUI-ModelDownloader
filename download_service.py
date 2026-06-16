"""Standalone aiohttp service wrapper around DownloadManager."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from aiohttp import web

try:
    from .download_manager import DEFAULT_COMMON_FOLDERS, DownloadManager
except ImportError:
    from download_manager import DEFAULT_COMMON_FOLDERS, DownloadManager

logger = logging.getLogger(__name__)


class DownloadService:
    """A small aiohttp server that exposes DownloadManager operations."""

    def __init__(
        self,
        comfyui_base: Path | str,
        port: int = 8189,
        host: str = "127.0.0.1",
        extra_folders: list[str] | None = None,
        chunk_size: int = 8192,
    ) -> None:
        self.manager = DownloadManager(comfyui_base=comfyui_base, extra_folders=extra_folders, chunk_size=chunk_size)
        self.port = port
        self.host = host
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

    async def handle_folders(self, request: web.Request) -> web.Response:
        return self._json_response(self.manager.list_folders())

    async def handle_download(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return self._json_response({"error": "Invalid JSON"}, status=400)

        entry, ok, error = await self.manager.start_download(
            url=payload.get("url", ""),
            folder_name=payload.get("folder", ""),
            custom_filename=payload.get("filename"),
            overwrite=bool(payload.get("overwrite")),
        )
        if not ok:
            return self._json_response({"error": error}, status=400 if "already exists" in (error or "") else 400)
        return self._json_response(entry.to_dict(), status=202)

    async def handle_downloads(self, request: web.Request) -> web.Response:
        status_filter = request.query.get("status")
        return self._json_response(await self.manager.list_downloads(status_filter=status_filter))

    async def handle_progress(self, request: web.Request) -> web.Response:
        download_id = request.match_info["download_id"]
        entry = self.manager.get_download(download_id)
        if not entry:
            return self._json_response({"error": "Download not found"}, status=404)
        return self._json_response(entry.to_dict())

    async def handle_cancel(self, request: web.Request) -> web.Response:
        download_id = request.match_info["download_id"]
        ok, error = self.manager.cancel_download(download_id)
        if not ok:
            status = 404 if error and "not found" in error else 409
            return self._json_response({"error": error}, status=status)
        return self._json_response({"status": "cancellation requested"})

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
    """Create a standalone service with sane defaults inferred from the environment."""
    comfy_path: Path | None = None
    if os.environ.get("COMFYUI_PATH"):
        comfy_path = Path(os.environ["COMFYUI_PATH"]).resolve()
    else:
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
