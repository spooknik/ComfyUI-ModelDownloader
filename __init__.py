"""ComfyUI custom-node loader for Model Downloader."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .download_service import create_default_service

logger = logging.getLogger(__name__)

NODE_CLASS_MAPPINGS: dict[str, type] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
WEB_DIRECTORY = os.path.join(os.path.dirname(os.path.realpath(__file__)), "js")


def _start_service() -> None:
    """Start the download service as a background daemon thread."""
    try:
        service = create_default_service()
        service.start_in_thread()
    except Exception as exc:
        logger.error("Failed to start ComfyUI-ModelDownloader service: %s", exc)


# Only auto-start the service when this file is loaded by ComfyUI (not on import from CLI tests).
if __name__ != "__main__":
    _start_service()
