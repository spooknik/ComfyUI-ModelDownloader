"""ComfyUI custom-node loader for ComfyUI-SpookTools.

ComfyUI imports this folder as a package (whatever the folder is called), so the server code lives in the
`spooktools` subpackage and is imported relatively. Without ComfyUI's PromptServer (tests, CLI) importing this
module does nothing beyond defining the mappings below.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

NODE_CLASS_MAPPINGS: dict[str, type] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
WEB_DIRECTORY = os.path.join(os.path.dirname(os.path.realpath(__file__)), "js")


def _infer_comfyui_base() -> Path:
    if os.environ.get("COMFYUI_PATH"):
        return Path(os.environ["COMFYUI_PATH"]).resolve()
    # Typical layout: ComfyUI/custom_nodes/ComfyUI-SpookTools/__init__.py
    candidate = Path(__file__).resolve().parent.parent.parent
    if (candidate / "main.py").exists() or (candidate / "comfy").exists():
        return candidate
    return Path.cwd().resolve()


def _register_routes() -> None:
    """Register the SpookTools API on ComfyUI's PromptServer if available."""
    try:
        from server import PromptServer
    except Exception:
        logger.debug("PromptServer not available; skipping route registration")
        return

    server = getattr(PromptServer, "instance", None)
    if server is None:
        logger.debug("PromptServer has no instance; skipping route registration")
        return

    from .spooktools.download_manager import DownloadManager
    from .spooktools.routes import register_routes

    register_routes(server.app, DownloadManager(comfyui_base=_infer_comfyui_base()))


_register_routes()
