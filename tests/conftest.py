"""Shared fixtures. pytest.ini puts the repo root on sys.path, so tests import the package as `spooktools`."""

from pathlib import Path

import pytest


@pytest.fixture
def comfy(tmp_path: Path) -> Path:
    """A fake ComfyUI base dir with a few model files (and a file outside models/ that must stay untouchable)."""
    loras = tmp_path / "models" / "loras"
    (loras / "sdxl").mkdir(parents=True)
    (loras / "a.safetensors").write_bytes(b"a" * 10)
    (loras / "sdxl" / "b.safetensors").write_bytes(b"b" * 20)
    (loras / ".hidden").write_bytes(b"x")
    (tmp_path / "main.py").write_text("# outside models")
    return tmp_path
