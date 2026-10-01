"""System routes: live resource stats for the System tab, and restarting ComfyUI."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from . import restart, system_info
from .download_manager import DownloadManager
from .http_helpers import json_response, read_json

logger = logging.getLogger(__name__)

# Seconds between answering a restart request and restarting, so the 202 response reaches the browser first.
RESTART_DELAY = 0.5
# Content types an HTML <form> can POST cross-site without a CORS preflight. A JSON API never needs them, so
# rejecting them stops a malicious page from restarting ComfyUI via a hidden form (as ComfyUI-Manager does).
FORM_CONTENT_TYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data", "text/plain"})


def routes(manager: DownloadManager) -> list[web.RouteDef]:
    """Route definitions with paths relative to the API prefix."""
    system_info.prime()
    restart_scheduled = False

    async def api_stats(request: web.Request) -> web.Response:
        loop = asyncio.get_running_loop()
        return json_response(await loop.run_in_executor(None, system_info.collect, manager.comfyui_base))

    def run_restart() -> None:
        nonlocal restart_scheduled
        try:
            restart.perform_restart()
        except Exception:
            # exec/exit failed (e.g. the interpreter path vanished): stay up and let the user try again.
            logger.exception("ComfyUI-SpookTools: restart failed; ComfyUI keeps running")
            restart_scheduled = False

    async def api_restart(request: web.Request) -> web.Response:
        nonlocal restart_scheduled
        if request.content_type in FORM_CONTENT_TYPES:
            return json_response({"error": "Send JSON (Content-Type: application/json) or no body"}, status=415)
        payload = {}
        if request.can_read_body:
            payload, bad_request = await read_json(request)
            if bad_request is not None:
                return bad_request
            if not isinstance(payload, dict):
                return json_response({"error": "Expected a JSON object"}, status=400)
        force = payload.get("force") is True

        queue = system_info.queue_state()
        if not force and queue and queue["remaining"]:
            return json_response({"error": "ComfyUI is busy", **queue}, status=409)

        mode = restart.restart_mode()
        if not restart_scheduled:
            restart_scheduled = True
            logger.warning(
                "ComfyUI-SpookTools: restart requested by %s (force=%s, queue=%s, mode=%s); restarting in %.1fs",
                request.remote,
                force,
                queue,
                mode,
                RESTART_DELAY,
            )
            # Looked up at call time (not bound now), so tests can stub restart.perform_restart.
            asyncio.get_running_loop().call_later(RESTART_DELAY, run_restart)
        return json_response({"status": "restarting", "boot_id": system_info.BOOT_ID, "mode": mode}, status=202)

    return [
        web.get("/system/stats", api_stats),
        web.post("/system/restart", api_restart),
    ]
