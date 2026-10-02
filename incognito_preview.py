"""Incognito image preview node.

The stock ComfyUI ``Preview Image`` node writes a PNG into the temp output
directory, which then shows up in media assets / file listings. This node
delivers the image to the browser entirely out-of-band: it encodes each frame
to PNG in memory and attaches a base64 data URI to the node's UI payload.
Nothing is written to disk and nothing is staged in any server-side folder.

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

# Cap on encoded size, mostly to keep the websocket payload sane if someone
# pipes a huge batch through. ~8M pixels (e.g. 2048x4096) of float32 RGB.
_MAX_PIXELS = 8_000_000


class SpookIncognitoPreview:
    """Preview an image without saving it anywhere (output node)."""

    # Must not clash with any built-in or other custom node class name.
    NODE_ID = "SpookIncognitoPreview"

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {"required": {"images": ("IMAGE",)}}

    RETURN_TYPES: tuple = ()
    FUNCTION = "preview"
    OUTPUT_NODE = True
    CATEGORY = "SpookTools/image"
    DESCRIPTION = (
        "Preview image without writing it to the output/temp folders â€” "
        "the pixels go straight to the browser as a data URI."
    )

    def preview(self, images: Any) -> dict[str, Any]:
        # Lazy: torch/PIL only exist inside ComfyUI at execution time.
        try:
            from PIL import Image
        except Exception as exc:  # pragma: no cover - ComfyUI always has PIL
            logger.error("Incognito preview requires Pillow: %s", exc)
            return {"ui": {"incognito_error": [str(exc)]}}

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
            img = Image.fromarray(arr, mode="RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG", compress_level=4)
            data_uris.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"))

        return {"ui": {"incognito_images": data_uris}}


def _u8():
    import torch

    return torch.uint8


NODE_CLASS_MAPPINGS = {SpookIncognitoPreview.NODE_ID: SpookIncognitoPreview}
NODE_DISPLAY_NAME_MAPPINGS = {SpookIncognitoPreview.NODE_ID: "Incognito Preview ðŸ•µ (no save)"}
