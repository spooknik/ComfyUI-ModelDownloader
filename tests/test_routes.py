"""HTTP tests through the real route registration (`register_routes`), plus the ComfyUI loader."""

import asyncio
import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from spooktools.download_manager import DownloadManager
from spooktools.routes import API_PREFIX, LEGACY_API_PREFIX, register_routes

API = API_PREFIX
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def make_client(comfy: Path):
    """Factory for a TestClient on a fresh `web.Application(**app_kwargs)` with all SpookTools routes."""

    @contextlib.asynccontextmanager
    async def factory(**app_kwargs):
        app = web.Application(**app_kwargs)
        register_routes(app, DownloadManager(comfyui_base=comfy))
        async with TestClient(TestServer(app)) as client:
            yield client

    return factory


def test_http_endpoints(comfy: Path, make_client):
    async def scenario():
        async with make_client() as client:
            payload = b"\x00\x01" * (3 << 20)  # 6 MB, streamed in several chunks
            resp = await client.post(
                f"{API}/upload",
                params={"folder": "loras", "filename": "up.safetensors"},
                data=payload,
                headers={"Content-Type": "application/octet-stream"},
            )
            assert resp.status == 201, await resp.text()
            assert (comfy / "models" / "loras" / "up.safetensors").read_bytes() == payload

            resp = await client.post(
                f"{API}/upload", params={"folder": "loras", "filename": "up.safetensors"}, data=b"x"
            )
            assert resp.status == 409

            resp = await client.get(f"{API}/files", params={"folder": "loras"})
            files = {f["path"]: f["size"] for f in (await resp.json())["files"]}
            assert files["up.safetensors"] == len(payload)

            resp = await client.delete(f"{API}/files", params={"folder": "loras", "path": "up.safetensors"})
            assert resp.status == 200
            resp = await client.delete(f"{API}/files", params={"folder": "loras", "path": "up.safetensors"})
            assert resp.status == 404
            resp = await client.delete(f"{API}/files", params={"folder": "..", "path": "main.py"})
            assert resp.status == 400
            assert (comfy / "main.py").exists()

    asyncio.run(scenario())


def test_chunked_http_endpoints(comfy: Path, make_client):
    async def scenario():
        async with make_client() as client:
            payload = bytes(range(256)) * 40_000  # ~10 MB
            resp = await client.post(
                f"{API}/upload/start", json={"folder": "vae", "filename": "big.bin", "size": len(payload)}
            )
            assert resp.status == 201
            uid = (await resp.json())["upload_id"]

            step = 3_000_000
            for offset in range(0, len(payload), step):
                resp = await client.post(
                    f"{API}/upload/{uid}/chunk", params={"offset": offset}, data=payload[offset : offset + step]
                )
                assert resp.status == 200, await resp.text()
                body = await resp.json()
            assert body["done"]
            assert (comfy / "models" / "vae" / "big.bin").read_bytes() == payload

            resp = await client.get(f"{API}/upload/{uid}")
            assert resp.status == 404

            resp = await client.post(f"{API}/upload/start", json={"folder": "vae", "filename": "c.bin", "size": 4})
            uid = (await resp.json())["upload_id"]
            resp = await client.post(f"{API}/upload/{uid}/chunk", params={"offset": 2}, data=b"zz")
            assert resp.status == 409 and (await resp.json())["received"] == 0
            resp = await client.get(f"{API}/upload/{uid}")
            assert (await resp.json())["received"] == 0
            resp = await client.delete(f"{API}/upload/{uid}")
            assert resp.status == 200
            assert not (comfy / "models" / "vae" / "c.bin.tmp").exists()

    asyncio.run(scenario())


def test_http_misc_endpoints(make_client):
    async def scenario():
        async with make_client() as client:
            resp = await client.get(f"{API}/folders")
            assert resp.status == 200
            names = {f["name"]: f["exists"] for f in (await resp.json())["folders"]}
            assert names["loras"] is True and names["checkpoints"] is False

            resp = await client.post(f"{API}/download", data=b"not json")
            assert resp.status == 400 and (await resp.json())["error"] == "Invalid JSON"
            resp = await client.post(f"{API}/upload/start", data=b"not json")
            assert resp.status == 400
            resp = await client.post(f"{API}/download", json={"url": "ftp://x/y.bin", "folder": "loras"})
            assert resp.status == 400
            resp = await client.post(f"{API}/upload/abc/chunk", params={"offset": "nope"}, data=b"x")
            assert resp.status == 400 and (await resp.json())["error"] == "Invalid offset"

            resp = await client.get(f"{API}/downloads")
            assert resp.status == 200 and await resp.json() == []
            resp = await client.get(f"{API}/progress/missing")
            assert resp.status == 404
            resp = await client.delete(f"{API}/download/missing")
            assert resp.status == 404

    asyncio.run(scenario())


def test_every_route_is_aliased_under_legacy_prefix(comfy: Path):
    app = web.Application()
    register_routes(app, DownloadManager(comfyui_base=comfy))
    by_prefix: dict[str, set[tuple[str, str]]] = {API_PREFIX: set(), LEGACY_API_PREFIX: set()}
    for route in app.router.routes():
        path = route.resource.canonical
        prefix = next(p for p in by_prefix if path.startswith(p + "/"))
        by_prefix[prefix].add((route.method, path[len(prefix) :]))
    assert len(by_prefix[API_PREFIX]) >= 12
    assert by_prefix[API_PREFIX] == by_prefix[LEGACY_API_PREFIX]


def test_legacy_prefix_shares_state(comfy: Path, make_client):
    """Old-prefix requests hit the same manager, so an upload started on one prefix can finish on the other."""

    async def scenario():
        async with make_client() as client:
            resp = await client.get(f"{LEGACY_API_PREFIX}/folders")
            assert resp.status == 200
            assert await resp.json() == await (await client.get(f"{API}/folders")).json()

            resp = await client.post(
                f"{LEGACY_API_PREFIX}/upload/start", json={"folder": "loras", "filename": "mix.bin", "size": 6}
            )
            assert resp.status == 201
            uid = (await resp.json())["upload_id"]
            resp = await client.post(f"{API}/upload/{uid}/chunk", params={"offset": 0}, data=b"abc")
            assert resp.status == 200 and not (await resp.json())["done"]
            resp = await client.post(f"{LEGACY_API_PREFIX}/upload/{uid}/chunk", params={"offset": 3}, data=b"def")
            assert resp.status == 200 and (await resp.json())["done"]
            assert (comfy / "models" / "loras" / "mix.bin").read_bytes() == b"abcdef"

            resp = await client.delete(f"{LEGACY_API_PREFIX}/files", params={"folder": "loras", "path": "mix.bin"})
            assert resp.status == 200 and not (comfy / "models" / "loras" / "mix.bin").exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("prefix", [API_PREFIX, LEGACY_API_PREFIX])
def test_upload_bypasses_client_max_size(comfy: Path, make_client, prefix: str):
    """ComfyUI sets client_max_size (--max-upload-size, default 100 MB); streamed uploads must not hit it."""

    async def scenario():
        async with make_client(client_max_size=1024) as client:
            resp = await client.post(
                f"{prefix}/upload", params={"folder": "loras", "filename": "big.bin"}, data=b"z" * 100_000
            )
            assert resp.status == 201, await resp.text()

            resp = await client.post(
                f"{prefix}/upload/start", json={"folder": "loras", "filename": "big2.bin", "size": 100_000}
            )
            uid = (await resp.json())["upload_id"]
            resp = await client.post(f"{prefix}/upload/{uid}/chunk", params={"offset": 0}, data=b"y" * 100_000)
            assert resp.status == 200, await resp.text()
            assert (await resp.json())["done"]

    asyncio.run(scenario())
    assert (comfy / "models" / "loras" / "big.bin").stat().st_size == 100_000
    assert (comfy / "models" / "loras" / "big2.bin").stat().st_size == 100_000


# ---- ComfyUI loader (repo-root __init__.py) -----------------------------------------------------------------


@pytest.fixture
def load_plugin():
    """Import the repo-root __init__.py the way ComfyUI does: as a package under an arbitrary folder name."""
    loaded: list[str] = []

    def load(name: str) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(
            name, REPO_ROOT / "__init__.py", submodule_search_locations=[str(REPO_ROOT)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        loaded.append(name)
        spec.loader.exec_module(module)
        return module

    yield load
    for name in loaded:
        for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
            del sys.modules[key]


def test_loader_without_promptserver(monkeypatch, load_plugin):
    monkeypatch.setitem(sys.modules, "server", None)  # `import server` raises ImportError
    module = load_plugin("ComfyUI-SpookTools")
    assert module.NODE_CLASS_MAPPINGS == {} and module.NODE_DISPLAY_NAME_MAPPINGS == {}
    assert Path(module.WEB_DIRECTORY) == REPO_ROOT / "js"
    assert "ComfyUI-SpookTools.spooktools" not in sys.modules  # Nothing server-side was imported.


@pytest.mark.parametrize("folder_name", ["ComfyUI-SpookTools", "ComfyUI-ModelDownloader"])
def test_loader_registers_routes_on_promptserver(comfy: Path, monkeypatch, load_plugin, folder_name: str):
    app = web.Application(client_max_size=1024)
    fake_server = types.ModuleType("server")
    fake_server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(app=app))
    monkeypatch.setitem(sys.modules, "server", fake_server)
    monkeypatch.setenv("COMFYUI_PATH", str(comfy))
    load_plugin(folder_name)

    paths = {route.resource.canonical for route in app.router.routes()}
    assert f"{API_PREFIX}/folders" in paths and f"{LEGACY_API_PREFIX}/folders" in paths

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"{API_PREFIX}/folders")
            assert resp.status == 200 and Path((await resp.json())["base"]) == comfy.resolve()
            resp = await client.post(
                f"{API_PREFIX}/upload", params={"folder": "loras", "filename": "via_loader.bin"}, data=b"q" * 5000
            )
            assert resp.status == 201, await resp.text()

    asyncio.run(scenario())
    assert (comfy / "models" / "loras" / "via_loader.bin").stat().st_size == 5000
