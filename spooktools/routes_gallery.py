"""Gallery routes: browse, preview, bulk-delete and zip-download files in ComfyUI's output/ and input/ folders."""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
from urllib.parse import quote

from aiohttp import web

from .download_manager import DownloadManager
from .gallery import DEFAULT_PAGE_SIZE, ChunkPipe, Gallery, GalleryError, ThumbnailError, content_type_for
from .http_helpers import error_response, json_response, read_json

logger = logging.getLogger(__name__)

# Long-lived caching only when the URL names the file version (`v=<mtime>`, which the browser panel always sends):
# then an overwritten file gets a new URL. Without it the browser must revalidate (cheap, via the ETag).
THUMB_CACHE_VERSIONED = "private, max-age=31536000, immutable"
THUMB_CACHE_UNVERSIONED = "private, no-cache"


def _flag(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _content_disposition(kind: str, filename: str) -> str:
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "_").replace("?", "_")
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def routes(manager: DownloadManager, gallery: Gallery | None = None) -> list[web.RouteDef]:
    """Route definitions with paths relative to the API prefix."""
    gallery = gallery or Gallery(manager.comfyui_base)

    async def run_blocking(func, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(None, functools.partial(func, *args, **kwargs))

    async def api_list(request: web.Request) -> web.Response:
        query = request.query
        try:
            offset = int(query.get("offset") or 0)
            limit = int(query.get("limit") or DEFAULT_PAGE_SIZE)
        except ValueError:
            return json_response({"error": "Invalid offset or limit"}, status=400)
        try:
            data = await run_blocking(
                gallery.list_files,
                query.get("root", ""),
                query.get("subfolder", ""),
                recursive=_flag(query.get("recursive")),
                query=query.get("q", ""),
                sort=query.get("sort") or "newest",
                kind=query.get("kind") or "all",
                offset=offset,
                limit=limit,
                paths_only=_flag(query.get("paths_only")),
            )
        except GalleryError as exc:
            return error_response(str(exc))
        return json_response(data)

    async def api_thumb(request: web.Request) -> web.Response:
        query = request.query
        try:
            size = gallery.thumb_size(int(query.get("size") or 256))
        except ValueError:
            return json_response({"error": "Invalid size"}, status=400)
        try:
            key, source, cache, data = await run_blocking(
                gallery.thumbnail_lookup, query.get("root", ""), query.get("path", ""), size
            )
        except GalleryError as exc:
            return error_response(str(exc))
        except ThumbnailError as exc:
            return json_response({"error": str(exc)}, status=415)

        etag = f'"{key}"'
        headers = {
            "ETag": etag,
            "Cache-Control": THUMB_CACHE_VERSIONED if query.get("v") else THUMB_CACHE_UNVERSIONED,
            "X-Content-Type-Options": "nosniff",
        }
        if etag in request.headers.get("If-None-Match", ""):
            return web.Response(status=304, headers=headers)
        if data is None:
            try:
                data = await asyncio.get_running_loop().run_in_executor(
                    gallery.thumb_executor, gallery.render_thumbnail, key, source, cache, size
                )
            except ThumbnailError as exc:
                return json_response({"error": str(exc)}, status=415)
        return web.Response(body=data, content_type=gallery.thumb_content_type, headers=headers)

    async def api_file(request: web.Request) -> web.StreamResponse:
        try:
            path, _ = await run_blocking(
                gallery.existing_file, request.query.get("root", ""), request.query.get("path", "")
            )
        except GalleryError as exc:
            return error_response(str(exc))
        content_type, inline = content_type_for(path.name)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": content_type,
                "Content-Disposition": _content_disposition("inline" if inline else "attachment", path.name),
                "X-Content-Type-Options": "nosniff",
                # User files are served from ComfyUI's origin: never let one run script there.
                "Content-Security-Policy": "sandbox",
                "Cache-Control": "private, no-cache",
            },
        )

    async def api_delete(request: web.Request) -> web.Response:
        payload, bad_request = await read_json(request)
        if bad_request is not None:
            return bad_request
        if not isinstance(payload, dict):
            return json_response({"error": "Expected a JSON object"}, status=400)
        try:
            data = await run_blocking(gallery.delete_files, payload.get("root", ""), payload.get("paths"))
        except GalleryError as exc:
            return error_response(str(exc))
        if data["deleted"]:
            logger.info("Gallery: deleted %d file(s) from %s", len(data["deleted"]), payload.get("root"))
        return json_response(data)

    async def api_zip_prepare(request: web.Request) -> web.Response:
        payload, bad_request = await read_json(request)
        if bad_request is not None:
            return bad_request
        if not isinstance(payload, dict):
            return json_response({"error": "Expected a JSON object"}, status=400)
        try:
            job, missing = await run_blocking(gallery.prepare_zip, payload.get("root", ""), payload.get("paths"))
        except GalleryError as exc:
            return error_response(str(exc))
        token = gallery.store_zip_job(job)
        return json_response(
            {
                "token": token,
                "count": len(job.paths),
                "total_size": job.total_size,
                "filename": job.filename,
                "missing": missing,
            },
            status=201,
        )

    async def api_zip_download(request: web.Request) -> web.StreamResponse:
        job = gallery.take_zip_job(request.match_info["token"])
        if job is None:
            return json_response({"error": "Download link not found or expired"}, status=404)

        response = web.StreamResponse(
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": _content_disposition("attachment", job.filename),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            }
        )
        await response.prepare(request)
        pipe = ChunkPipe(asyncio.get_running_loop())
        worker = threading.Thread(target=gallery.write_zip, args=(job, pipe), name="spooktools-zip", daemon=True)
        worker.start()
        try:
            while True:
                chunk = await pipe.get()
                if chunk is ChunkPipe.END:
                    break
                if isinstance(chunk, BaseException):
                    # Headers are gone already; failing the request drops the connection without the final
                    # chunk, so the browser reports a failed download instead of saving a truncated zip.
                    raise RuntimeError("Zip stream failed") from chunk
                await response.write(chunk)
            await response.write_eof()
        except ConnectionResetError:  # Client went away (cancelled download, closed tab).
            logger.debug("Zip download %s aborted by the client", job.filename)
        finally:
            pipe.cancel()  # Stops the zip thread within ZIP_POLL_SECONDS if it is still running.
        return response

    return [
        web.get("/gallery/list", api_list),
        web.get("/gallery/thumb", api_thumb),
        web.get("/gallery/file", api_file),
        web.post("/gallery/delete", api_delete),
        web.post("/gallery/zip", api_zip_prepare),
        # No implicit HEAD route: a HEAD request must not use up a download token or start a zip thread.
        web.get("/gallery/zip/{token}", api_zip_download, allow_head=False),
    ]
