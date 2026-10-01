"""ComfyUI custom-node loader for Model Downloader."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

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
        from .download_manager import DownloadManager, error_status
    except ImportError:
        from download_manager import DownloadManager, error_status

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
            return json_response({"error": error}, status=error_status(error))
        return json_response(entry.to_dict(), status=202)

    async def api_cancel(request: web.Request) -> web.Response:
        download_id = request.match_info["download_id"]
        ok, error = _DOWNLOAD_MANAGER.cancel_download(download_id)
        if not ok:
            return json_response({"error": error}, status=error_status(error))
        return json_response({"status": "cancellation requested"})

    async def api_files(request: web.Request) -> web.Response:
        data, error = await _DOWNLOAD_MANAGER.list_files_async(request.query.get("folder", ""))
        if error:
            return json_response({"error": error}, status=error_status(error))
        return json_response(data)

    async def api_delete_file(request: web.Request) -> web.Response:
        ok, error = _DOWNLOAD_MANAGER.delete_file(request.query.get("folder", ""), request.query.get("path", ""))
        if not ok:
            return json_response({"error": error}, status=error_status(error))
        return json_response({"status": "deleted"})

    async def api_upload(request: web.Request) -> web.Response:
        # Raw request body, streamed: request.content bypasses ComfyUI's --max-upload-size (client_max_size),
        # which only applies to fully-buffered reads.
        data, error = await _DOWNLOAD_MANAGER.save_upload(
            folder_name=request.query.get("folder", ""),
            filename=request.query.get("filename", ""),
            chunks=request.content.iter_chunked(1 << 20),
            expected_size=request.content_length,
            overwrite=request.query.get("overwrite") in ("1", "true"),
        )
        if error:
            return json_response({"error": error}, status=error_status(error))
        return json_response(data, status=201)

    async def api_upload_start(request: web.Request) -> web.Response:
        import json as _json

        try:
            payload = await request.json()
        except _json.JSONDecodeError:
            return json_response({"error": "Invalid JSON"}, status=400)
        data, error = _DOWNLOAD_MANAGER.start_upload(
            folder_name=payload.get("folder", ""),
            filename=payload.get("filename", ""),
            total=payload.get("size"),
            overwrite=bool(payload.get("overwrite")),
        )
        if error:
            return json_response({"error": error}, status=error_status(error))
        return json_response(data, status=201)

    async def api_upload_status(request: web.Request) -> web.Response:
        data, error = _DOWNLOAD_MANAGER.get_upload(request.match_info["upload_id"])
        if error:
            return json_response({"error": error}, status=error_status(error))
        return json_response(data)

    async def api_upload_chunk(request: web.Request) -> web.Response:
        try:
            offset = int(request.query.get("offset", ""))
        except ValueError:
            return json_response({"error": "Invalid offset"}, status=400)
        data, error = await _DOWNLOAD_MANAGER.upload_chunk(
            upload_id=request.match_info["upload_id"],
            offset=offset,
            chunks=request.content.iter_chunked(1 << 20),
            expected_size=request.content_length,
        )
        if error:
            return json_response({"error": error, **(data or {})}, status=error_status(error))
        return json_response(data)

    async def api_upload_abort(request: web.Request) -> web.Response:
        ok, error = _DOWNLOAD_MANAGER.abort_upload(request.match_info["upload_id"])
        if not ok:
            return json_response({"error": error}, status=error_status(error))
        return json_response({"status": "cancelled"})

    prefix = "/api/model-downloader"
    app.router.add_get(f"{prefix}/folders", api_folders)
    app.router.add_get(f"{prefix}/downloads", api_downloads)
    app.router.add_get(f"{prefix}/progress/{{download_id}}", api_progress)
    app.router.add_post(f"{prefix}/download", api_download)
    app.router.add_delete(f"{prefix}/download/{{download_id}}", api_cancel)
    app.router.add_get(f"{prefix}/files", api_files)
    app.router.add_delete(f"{prefix}/files", api_delete_file)
    app.router.add_post(f"{prefix}/upload", api_upload)
    app.router.add_post(f"{prefix}/upload/start", api_upload_start)
    app.router.add_get(f"{prefix}/upload/{{upload_id}}", api_upload_status)
    app.router.add_post(f"{prefix}/upload/{{upload_id}}/chunk", api_upload_chunk)
    app.router.add_delete(f"{prefix}/upload/{{upload_id}}", api_upload_abort)
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
