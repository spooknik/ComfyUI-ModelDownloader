"""Incognito image preview node.

The stock ComfyUI ``Preview Image`` node writes a PNG into the temp output
directory, which then shows up in media assets / file listings. This node
delivers the image to the browser entirely out-of-band: it encodes each frame
to PNG in memory and pushes base64 data URIs over the websocket as a custom
``spooktools.incognito_preview`` event, sent only to the client that queued
the prompt. js/incognito_preview.js draws them in a widget on the node.

The images deliberately do not go in the node's ``ui`` output: ComfyUI keeps
that in the prompt history (in RAM, served by /history, shown in the queue
sidebar), so the pixels would outlive the preview. Nothing is written to disk
and nothing is staged in any server-side folder.

``torch`` (and ``numpy``/``Pillow``) are only imported when the node executes;
ComfyUI always provides them at runtime, but keeping the import lazy lets this
package load in environments that don't have a GPU stack installed (tests, CI).
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

EVENT = "spooktools.incognito_preview"

# Cap on encoded size, mostly to keep the websocket payload sane if someone
# pipes a huge batch through. ~8M pixels (e.g. 2048x4096) of float32 RGB.
_MAX_PIXELS = 8_000_000


class SpookIncognitoPreview:
    """Preview an image without saving it anywhere (output node)."""

    # Must not clash with any built-in or other custom node class name.
    NODE_ID = "SpookIncognitoPreview"

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {"required": {"images": ("IMAGE",)}, "hidden": {"unique_id": "UNIQUE_ID"}}

    RETURN_TYPES: tuple = ()
    FUNCTION = "preview"
    OUTPUT_NODE = True
    CATEGORY = "SpookTools/image"
    DESCRIPTION = (
        "Preview image without writing it to the output/temp folders - "
        "the pixels go straight to the browser as a data URI."
    )

    def preview(self, images: Any, unique_id: str | None = None) -> dict[str, Any]:
        # Lazy: torch/PIL only exist inside ComfyUI at execution time.
        try:
            from PIL import Image
        except Exception as exc:  # pragma: no cover - ComfyUI always has PIL
            logger.error("Incognito preview requires Pillow: %s", exc)
            return {}

        data_uris: list[str] = []
        total_pixels = 0
        for frame in images:
            # frame: (H, W, 3) float tensor in [0, 1]
            h, w = int(frame.shape[0]), int(frame.shape[1])
            total_pixels += h * w
            if total_pixels > _MAX_PIXELS:
                logger.warning(
                    "Incognito preview: skipping remaining frames (batch exceeds %d pixels)",
                    _MAX_PIXELS,
                )
                break
            arr = frame.clamp(0.0, 1.0).mul(255.0).round().to(dtype=_u8(), device="cpu").numpy()
            img = Image.fromarray(arr)  # uint8 (H, W, 3) -> RGB; the mode= argument is deprecated in Pillow 11.3
            buf = io.BytesIO()
            img.save(buf, format="PNG", compress_level=4)
            data_uris.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"))

        _send(unique_id, data_uris)
        return {}


def _send(unique_id: str | None, data_uris: list[str]) -> None:
    try:
        from server import PromptServer
    except Exception:  # pragma: no cover - only outside ComfyUI
        logger.warning("Incognito preview: PromptServer unavailable; nothing to show the image on")
        return
    server = PromptServer.instance
    # client_id is the browser tab that queued this prompt; None (API-only prompts) broadcasts.
    server.send_sync(EVENT, {"node": unique_id, "images": data_uris}, server.client_id)


def _u8():
    import torch

    return torch.uint8


NODE_CLASS_MAPPINGS = {SpookIncognitoPreview.NODE_ID: SpookIncognitoPreview}
NODE_DISPLAY_NAME_MAPPINGS = {SpookIncognitoPreview.NODE_ID: "Incognito Preview (no save)"}
