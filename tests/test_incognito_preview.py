"""The Incognito Preview node pushes in-memory PNGs over the websocket and returns no UI output (no history)."""

import base64
import io
import sys
import types

import numpy as np
import pytest
from PIL import Image

import incognito_preview


class FakeTensor:
    """Just enough of torch.Tensor for the node's clamp/mul/round/to/numpy chain."""

    def __init__(self, array: np.ndarray):
        self.array = array
        self.shape = array.shape

    def clamp(self, lo, hi):
        return FakeTensor(np.clip(self.array, lo, hi))

    def mul(self, k):
        return FakeTensor(self.array * k)

    def round(self):
        return FakeTensor(np.round(self.array))

    def to(self, dtype, device):
        return FakeTensor(self.array.astype(dtype))

    def numpy(self):
        return self.array


@pytest.fixture
def server(monkeypatch):
    sent = []
    instance = types.SimpleNamespace(client_id="tab-1", send_sync=lambda *args: sent.append(args))
    prompt_server = types.SimpleNamespace(instance=instance)
    monkeypatch.setitem(sys.modules, "server", types.SimpleNamespace(PromptServer=prompt_server))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(uint8=np.uint8))
    return sent


def test_preview_sends_pngs_to_the_queuing_client(server):
    frame = np.zeros((4, 6, 3), dtype=np.float32)
    frame[..., 0] = 1.0  # red
    frame[0, 0] = [2.0, -1.0, 0.5]  # out-of-range values are clamped
    result = incognito_preview.SpookIncognitoPreview().preview([FakeTensor(frame)] * 2, unique_id="7:3")

    assert result == {}  # nothing in the `ui` output, so nothing lands in the prompt history
    [(event, data, sid)] = server
    assert event == incognito_preview.EVENT and sid == "tab-1" and data["node"] == "7:3"
    assert len(data["images"]) == 2
    prefix = "data:image/png;base64,"
    assert data["images"][0].startswith(prefix)
    img = Image.open(io.BytesIO(base64.b64decode(data["images"][0][len(prefix) :])))
    assert img.size == (6, 4) and img.getpixel((1, 1)) == (255, 0, 0) and img.getpixel((0, 0)) == (255, 0, 128)


def test_preview_caps_huge_batches(server, monkeypatch):
    monkeypatch.setattr(incognito_preview, "_MAX_PIXELS", 50)
    frame = FakeTensor(np.zeros((4, 6, 3), dtype=np.float32))  # 24 px each: only two fit under 50
    incognito_preview.SpookIncognitoPreview().preview([frame] * 5, unique_id="1")
    assert len(server[0][1]["images"]) == 2
