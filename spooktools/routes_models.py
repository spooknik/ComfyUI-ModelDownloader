"""Model routes: URL downloads, uploads (single-request and chunked) and the model file manager."""

from __future__ import annotations

from aiohttp import web

from .download_manager import DownloadManager
from .http_helpers import error_response, json_response, read_json


def routes(manager: DownloadManager) -> list[web.RouteDef]:
    """Route definitions with paths relative to the API prefix."""

    async def api_folders(request: web.Request) -> web.Response:
        return json_response(manager.list_folders())

    async def api_downloads(request: web.Request) -> web.Response:
        status_filter = request.query.get("status")
        return json_response(await manager.list_downloads(status_filter=status_filter))

    async def api_progress(request: web.Request) -> web.Response:
        entry = manager.get_download(request.match_info["download_id"])
        if not entry:
            return json_response({"error": "Download not found"}, status=404)
        return json_response(entry.to_dict())

    async def api_download(request: web.Request) -> web.Response:
        payload, bad_request = await read_json(request)
        if bad_request is not None:
            return bad_request
        entry, ok, error = await manager.start_download(
            url=payload.get("url", ""),
            folder_name=payload.get("folder", ""),
            custom_filename=payload.get("filename"),
            overwrite=bool(payload.get("overwrite")),
        )
        if not ok:
            return error_response(error)
        return json_response(entry.to_dict(), status=202)

    async def api_cancel(request: web.Request) -> web.Response:
        ok, error = manager.cancel_download(request.match_info["download_id"])
        if not ok:
            return error_response(error)
        return json_response({"status": "cancellation requested"})

    async def api_files(request: web.Request) -> web.Response:
        data, error = await manager.list_files_async(request.query.get("folder", ""))
        if error:
            return error_response(error)
        return json_response(data)

    async def api_delete_file(request: web.Request) -> web.Response:
        ok, error = manager.delete_file(request.query.get("folder", ""), request.query.get("path", ""))
        if not ok:
            return error_response(error)
        return json_response({"status": "deleted"})

    async def api_upload(request: web.Request) -> web.Response:
        # Raw request body, streamed: request.content bypasses ComfyUI's --max-upload-size (client_max_size),
        # which only applies to fully-buffered reads.
        data, error = await manager.save_upload(
            folder_name=request.query.get("folder", ""),
            filename=request.query.get("filename", ""),
            chunks=request.content.iter_chunked(1 << 20),
            expected_size=request.content_length,
            overwrite=request.query.get("overwrite") in ("1", "true"),
        )
        if error:
            return error_response(error)
        return json_response(data, status=201)

    async def api_upload_start(request: web.Request) -> web.Response:
        payload, bad_request = await read_json(request)
        if bad_request is not None:
            return bad_request
        data, error = manager.start_upload(
            folder_name=payload.get("folder", ""),
            filename=payload.get("filename", ""),
            total=payload.get("size"),
            overwrite=bool(payload.get("overwrite")),
        )
        if error:
            return error_response(error)
        return json_response(data, status=201)

    async def api_upload_status(request: web.Request) -> web.Response:
        data, error = manager.get_upload(request.match_info["upload_id"])
        if error:
            return error_response(error)
        return json_response(data)

    async def api_upload_chunk(request: web.Request) -> web.Response:
        try:
            offset = int(request.query.get("offset", ""))
        except ValueError:
            return json_response({"error": "Invalid offset"}, status=400)
        data, error = await manager.upload_chunk(
            upload_id=request.match_info["upload_id"],
            offset=offset,
            chunks=request.content.iter_chunked(1 << 20),
            expected_size=request.content_length,
        )
        if error:
            # Carries the committed `received` count so the browser can resync after a failed chunk.
            return error_response(error, data)
        return json_response(data)

    async def api_upload_abort(request: web.Request) -> web.Response:
        ok, error = manager.abort_upload(request.match_info["upload_id"])
        if not ok:
            return error_response(error)
        return json_response({"status": "cancelled"})

    return [
        web.get("/folders", api_folders),
        web.get("/downloads", api_downloads),
        web.get("/progress/{download_id}", api_progress),
        web.post("/download", api_download),
        web.delete("/download/{download_id}", api_cancel),
        web.get("/files", api_files),
        web.delete("/files", api_delete_file),
        web.post("/upload", api_upload),
        # "/upload/start" must stay ahead of the "/upload/{upload_id}" routes.
        web.post("/upload/start", api_upload_start),
        web.get("/upload/{upload_id}", api_upload_status),
        web.post("/upload/{upload_id}/chunk", api_upload_chunk),
        web.delete("/upload/{upload_id}", api_upload_abort),
    ]
