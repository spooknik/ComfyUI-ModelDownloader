"""Small sanity test for URL normalization."""

import sys
from pathlib import Path

# Allow running from either inside the package or from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from download_manager import DownloadManager
except ImportError:
    from .download_manager import DownloadManager


def test_huggingface_blob_to_resolve():
    mgr = DownloadManager(".")
    blob = "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors"
    expected = "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors"
    assert mgr._normalize_url(blob) == expected


def test_huggingface_resolve_unchanged():
    mgr = DownloadManager(".")
    resolve = "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors"
    assert mgr._normalize_url(resolve) == resolve


def test_other_url_unchanged():
    mgr = DownloadManager(".")
    url = "https://example.com/model.safetensors"
    assert mgr._normalize_url(url) == url


if __name__ == "__main__":
    test_huggingface_blob_to_resolve()
    test_huggingface_resolve_unchanged()
    test_other_url_unchanged()
    print("URL normalization tests passed")
