"""ComfyUI custom-node loader for Model Downloader."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

NODE_CLASS_MAPPINGS: dict[str, type] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
WEB_DIRECTORY = os.path.join(os.path.dirname(os.path.realpath(__file__)), "js")


_COMFYUI_BASE: Path | None = None
_DOWNLOAD_MANAGER: Any | None = None
_SERVICE_THREAD: Any | None = None


def _infer_comfyui_base() -> Path:
    if os.environ.get("COMFYUI_PATH"):
        return Path(os.environ["COMFYUI_PATH"]).resolve()
    # Typical layout: custom_nodes/ComfyUI-ModelDownloader/../../..
    candidate = Path(__file__).resolve().parent.parent.parent
    if (candidate / "main.py").exists() or (candidate / "comfy").exists():
        return candidate
    return Path.cwd().resolve()


def _register_routes() -> None:
    """Register download endpoints on ComfyUI's PromptServer if available."""
    global _DOWNLOAD_MANAGER

    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        logger.debug("PromptServer not available; skipping route registration")
        return

    server = getattr(PromptServer, "instance", None)
    if server is None:
        logger.debug("PromptServer has no instance; skipping route registration")
        return

    base = _infer_comfyui_base()
    try:
        from .download_manager import DownloadManager
    except ImportError:
        from download_manager import DownloadManager

    _DOWNLOAD_MANAGER = DownloadManager(comfyui_base=base)
    app: web.Application = server.app

    def json_response(data, status: int = 200) -> web.Response:
        return web.json_response(data, status=status)

    async def api_folders(request: web.Request) -> web.Response:
        return json_response(_DOWNLOAD_MANAGER.list_folders())

    async def api_downloads(request: web.Request) -> web.Response:
        status_filter = request.query.get("status")
        return json_response(await _DOWNLOAD_MANAGER.list_downloads(status_filter=status_filter))

    async def api_progress(request: web.Request) -> web.Response:
        download_id = request.match_info["download_id"]
        entry = _DOWNLOAD_MANAGER.get_download(download_id)
        if not entry:
            return json_response({"error": "Download not found"}, status=404)
        return json_response(entry.to_dict())

    async def api_download(request: web.Request) -> web.Response:
        import json as _json

        try:
            payload = await request.json()
        except _json.JSONDecodeError:
            return json_response({"error": "Invalid JSON"}, status=400)

        entry, ok, error = await _DOWNLOAD_MANAGER.start_download(
            url=payload.get("url", ""),
            folder_name=payload.get("folder", ""),
            custom_filename=payload.get("filename"),
            overwrite=bool(payload.get("overwrite")),
        )
        if not ok:
            # Use 409 when the file already exists.
            status = 409 if error and "already exists" in error else 400
            return json_response({"error": error}, status=status)
        return json_response(entry.to_dict(), status=202)

    async def api_cancel(request: web.Request) -> web.Response:
        download_id = request.match_info["download_id"]
        ok, error = _DOWNLOAD_MANAGER.cancel_download(download_id)
        if not ok:
            status = 404 if error and "not found" in error else 409
            return json_response({"error": error}, status=status)
        return json_response({"status": "cancellation requested"})

    prefix = "/api/model-downloader"
    app.router.add_get(f"{prefix}/folders", api_folders)
    app.router.add_get(f"{prefix}/downloads", api_downloads)
    app.router.add_get(f"{prefix}/progress/{{download_id}}", api_progress)
    app.router.add_post(f"{prefix}/download", api_download)
    app.router.add_delete(f"{prefix}/download/{{download_id}}", api_cancel)
    logger.info("ComfyUI-ModelDownloader routes registered at %s", prefix)


def _start_standalone_service() -> None:
    """Start the standalone aiohttp download service in a background thread."""
    global _SERVICE_THREAD
    try:
        try:
            from .download_service import create_default_service
        except ImportError:
            from download_service import create_default_service

        service = create_default_service()
        _SERVICE_THREAD = service.start_in_thread()
    except Exception as exc:
        logger.error("Failed to start ComfyUI-ModelDownloader standalone service: %s", exc)


# Only auto-start / register when this file is loaded by ComfyUI (not on import from CLI tests).
if __name__ != "__main__":
    _register_routes()
    if os.environ.get("COMFY_MODEL_DL_STANDALONE", "1") != "0":
        _start_standalone_service()
