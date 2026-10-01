"""Small response helpers shared by the feature route modules."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from .download_manager import error_status


def json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def error_response(error: str | None, extra: dict[str, Any] | None = None) -> web.Response:
    """JSON ``{"error": ...}`` body with the HTTP status derived from the error message."""
    return json_response({"error": error, **(extra or {})}, status=error_status(error))


async def read_json(request: web.Request) -> tuple[Any, web.Response | None]:
    """Parse a JSON request body. Returns (payload, None), or (None, a 400 response) if the body is not JSON.

    Test the response with `is not None`: aiohttp responses are MutableMappings, so an empty one is falsy.
    """
    try:
        return await request.json(), None
    except json.JSONDecodeError:
        return None, json_response({"error": "Invalid JSON"}, status=400)
