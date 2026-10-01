"""Tests for the file manager (list/delete) and upload support."""

import asyncio
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_manager import DownloadManager, error_status  # noqa: E402
from download_service import DownloadService  # noqa: E402


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


@pytest.fixture
def comfy(tmp_path: Path) -> Path:
    loras = tmp_path / "models" / "loras"
    (loras / "sdxl").mkdir(parents=True)
    (loras / "a.safetensors").write_bytes(b"a" * 10)
    (loras / "sdxl" / "b.safetensors").write_bytes(b"b" * 20)
    (loras / ".hidden").write_bytes(b"x")
    (tmp_path / "main.py").write_text("# outside models")
    return tmp_path


def test_list_files_recursive(comfy: Path):
    data, error = DownloadManager(comfy).list_files("loras")
    assert error is None
    assert [f["path"] for f in data["files"]] == ["a.safetensors", "sdxl/b.safetensors"]
    assert data["total_size"] == 30


def test_list_missing_folder_is_empty(comfy: Path):
    data, error = DownloadManager(comfy).list_files("vae")
    assert error is None and data["files"] == []


@pytest.mark.parametrize("folder", ["..", ".", "", "../models", "loras/../..", "C:"])
def test_invalid_folder_rejected(comfy: Path, folder: str):
    mgr = DownloadManager(comfy)
    _, error = mgr.list_files(folder)
    assert error and "Invalid folder" in error
    ok, error = mgr.delete_file(folder, "main.py")
    assert not ok


@pytest.mark.parametrize("path", ["../../main.py", "..\\..\\main.py", "/etc/passwd", "C:/Windows/x", "", "sdxl/../../../main.py"])
def test_delete_path_traversal_rejected(comfy: Path, path: str):
    ok, error = DownloadManager(comfy).delete_file("loras", path)
    assert not ok and error_status(error) == 400
    assert (comfy / "main.py").exists()


def test_delete_file(comfy: Path):
    mgr = DownloadManager(comfy)
    ok, error = mgr.delete_file("loras", "sdxl/b.safetensors")
    assert ok, error
    assert not (comfy / "models" / "loras" / "sdxl" / "b.safetensors").exists()
    ok, error = mgr.delete_file("loras", "sdxl/b.safetensors")
    assert not ok and error_status(error) == 404


def test_delete_refuses_directory(comfy: Path):
    ok, error = DownloadManager(comfy).delete_file("loras", "sdxl")
    assert not ok and error_status(error) == 404
    assert (comfy / "models" / "loras" / "sdxl").is_dir()


def test_upload_and_overwrite(comfy: Path):
    mgr = DownloadManager(comfy)
    data, error = asyncio.run(mgr.save_upload("checkpoints", "new.safetensors", _chunks(b"12", b"345"), 5))
    assert error is None and data["size"] == 5
    dest = comfy / "models" / "checkpoints" / "new.safetensors"
    assert dest.read_bytes() == b"12345"

    _, error = asyncio.run(mgr.save_upload("checkpoints", "new.safetensors", _chunks(b"x"), 1))
    assert error_status(error) == 409
    _, error = asyncio.run(mgr.save_upload("checkpoints", "new.safetensors", _chunks(b"x"), 1, overwrite=True))
    assert error is None and dest.read_bytes() == b"x"


def test_upload_filename_sanitized(comfy: Path):
    data, error = asyncio.run(DownloadManager(comfy).save_upload("loras", "../../evil.py", _chunks(b"x")))
    assert error is None
    assert Path(data["destination"]).parent == comfy / "models" / "loras"
    assert not (comfy / "evil.py").exists()


def test_upload_incomplete_cleans_up(comfy: Path):
    mgr = DownloadManager(comfy)
    _, error = asyncio.run(mgr.save_upload("loras", "short.bin", _chunks(b"abc"), expected_size=10))
    assert error and "incomplete" in error
    folder = comfy / "models" / "loras"
    assert not (folder / "short.bin").exists()
    assert not (folder / "short.bin.tmp").exists()


def test_upload_stream_error_cleans_up(comfy: Path):
    async def broken():
        yield b"abc"
        raise ConnectionResetError("client went away")

    mgr = DownloadManager(comfy)
    _, error = asyncio.run(mgr.save_upload("loras", "broken.bin", broken()))
    assert error and "client went away" in error
    assert not (comfy / "models" / "loras" / "broken.bin.tmp").exists()
    assert not mgr._uploads


def test_upload_disk_space_check(comfy: Path):
    _, error = asyncio.run(DownloadManager(comfy).save_upload("loras", "huge.bin", _chunks(b"x"), 1 << 62))
    assert error_status(error) == 507


def test_active_upload_blocks_delete_and_listing_marks_it(comfy: Path):
    mgr = DownloadManager(comfy)

    async def scenario():
        gate = asyncio.Event()

        async def slow():
            yield b"part"
            await gate.wait()
            yield b"rest"

        task = asyncio.create_task(mgr.save_upload("loras", "a.safetensors", slow(), overwrite=True))
        await asyncio.sleep(0.1)
        ok, error = mgr.delete_file("loras", "a.safetensors")
        assert not ok and error_status(error) == 409
        listing, _ = mgr.list_files("loras")
        active = {f["path"] for f in listing["files"] if f["active"]}
        assert active == {"a.safetensors", "a.safetensors.tmp"}
        _, error = await mgr.save_upload("loras", "a.safetensors", _chunks(b"x"), overwrite=True)
        assert error_status(error) == 409
        gate.set()
        return await task

    data, error = asyncio.run(scenario())
    assert error is None and data["size"] == 8


def test_http_endpoints(comfy: Path):
    """End-to-end through the standalone aiohttp service (same handlers shape as the ComfyUI routes)."""

    async def scenario():
        service = DownloadService(comfyui_base=comfy)
        async with TestClient(TestServer(service._app)) as client:
            payload = b"\x00\x01" * (3 << 20)  # 6 MB, streamed in several chunks
            resp = await client.post(
                "/upload",
                params={"folder": "loras", "filename": "up.safetensors"},
                data=payload,
                headers={"Content-Type": "application/octet-stream"},
            )
            assert resp.status == 201, await resp.text()
            assert (comfy / "models" / "loras" / "up.safetensors").read_bytes() == payload

            resp = await client.post("/upload", params={"folder": "loras", "filename": "up.safetensors"}, data=b"x")
            assert resp.status == 409

            resp = await client.get("/files", params={"folder": "loras"})
            files = {f["path"]: f["size"] for f in (await resp.json())["files"]}
            assert files["up.safetensors"] == len(payload)

            resp = await client.delete("/files", params={"folder": "loras", "path": "up.safetensors"})
            assert resp.status == 200
            resp = await client.delete("/files", params={"folder": "loras", "path": "up.safetensors"})
            assert resp.status == 404
            resp = await client.delete("/files", params={"folder": "..", "path": "main.py"})
            assert resp.status == 400
            assert (comfy / "main.py").exists()

    asyncio.run(scenario())


def test_upload_bypasses_client_max_size(comfy: Path):
    """ComfyUI sets client_max_size (--max-upload-size, default 100 MB); streamed uploads must not hit it."""
    from aiohttp import web

    async def scenario():
        service = DownloadService(comfyui_base=comfy)
        app = web.Application(client_max_size=1024)
        app.router.add_post("/upload", service.handle_upload)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/upload", params={"folder": "loras", "filename": "big.bin"}, data=b"z" * 100_000)
            assert resp.status == 201, await resp.text()

    asyncio.run(scenario())
