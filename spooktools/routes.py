"""Registers every feature's HTTP routes on an aiohttp application, under each API prefix."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from aiohttp import web

from . import routes_models, routes_system
from .download_manager import DownloadManager

logger = logging.getLogger(__name__)

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
            [web.RouteDef(route.method, prefix + route.path, route.handler, route.kwargs) for route in route_defs]
        )
    logger.info("ComfyUI-SpookTools routes registered at %s", ", ".join(prefixes))
