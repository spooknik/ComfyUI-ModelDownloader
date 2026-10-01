"""Registers every feature's HTTP routes on an aiohttp application, under each API prefix."""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable, Iterable

from aiohttp import web

from . import routes_models, routes_system
from .download_manager import DownloadManager
from .http_helpers import json_response

logger = logging.getLogger(__name__)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

API_PREFIX = "/api/spooktools"
# Pre-rename prefix. Kept as an alias so a browser tab still running the old JS keeps working across an update.
LEGACY_API_PREFIX = "/api/model-downloader"

RouteFactory = Callable[[DownloadManager], Iterable[web.RouteDef]]

# Each feature module exposes `routes(manager)` returning route definitions with paths relative to the prefix.
# Adding a feature = one new module + one entry here.
FEATURES: tuple[RouteFactory, ...] = (
    routes_models.routes,
    routes_system.routes,
)


# Content types a page on another site can POST from a visitor's browser without a CORS preflight (HTML forms,
# `fetch(..., {mode: "no-cors"})`). Our frontend only ever sends application/json or application/octet-stream.
SIMPLE_CONTENT_TYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data", "text/plain"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def cross_site_guard(handler: Handler) -> Handler:
    """Refuse state-changing requests that another website could make from the browser of someone who can reach
    ComfyUI (CSRF): restart ComfyUI, start downloads, upload or delete files. Browsers mark such requests with
    `Sec-Fetch-Site: cross-site`; for older browsers, a POST must also carry a content type that only same-origin
    scripts (or non-browser clients like curl) can send without a preflight, which ComfyUI doesn't approve.
    """

    @functools.wraps(handler)
    async def guarded(request: web.Request) -> web.StreamResponse:
        if request.method in UNSAFE_METHODS:
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return json_response({"error": "Cross-site request refused"}, status=403)
            if request.method != "DELETE" and (
                "Content-Type" not in request.headers or request.content_type in SIMPLE_CONTENT_TYPES
            ):
                return json_response(
                    {"error": "Send Content-Type: application/json (or application/octet-stream for uploads)"},
                    status=415,
                )
        return await handler(request)

    return guarded


def register_routes(
    app: web.Application,
    manager: DownloadManager,
    prefixes: Iterable[str] = (API_PREFIX, LEGACY_API_PREFIX),
) -> None:
    """Add all feature routes to `app` once per prefix (same handlers, so state is shared between prefixes)."""
    prefixes = tuple(prefixes)
    route_defs = [route for feature in FEATURES for route in feature(manager)]
    for prefix in prefixes:
        app.router.add_routes(
            [
                web.RouteDef(route.method, prefix + route.path, cross_site_guard(route.handler), route.kwargs)
                for route in route_defs
            ]
        )
    logger.info("ComfyUI-SpookTools routes registered at %s", ", ".join(prefixes))
