"""Tests for the file manager (list/delete) and upload support."""

import asyncio
from pathlib import Path

import pytest

from spooktools import download_manager
from spooktools.download_manager import DownloadManager, error_status


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


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


def test_rename_file(comfy: Path):
    mgr = DownloadManager(comfy)
    loras = comfy / "models" / "loras"
    ok, error = mgr.rename_file("loras", "a.safetensors", "renamed.safetensors")
    assert ok, error
    assert (loras / "renamed.safetensors").read_bytes() == b"a" * 10
    assert not (loras / "a.safetensors").exists()
    # Moving into a new subfolder creates it.
    ok, error = mgr.rename_file("loras", "renamed.safetensors", "flux/moved.safetensors")
    assert ok, error
    assert (loras / "flux" / "moved.safetensors").is_file()


def test_rename_refuses_overwrite_and_missing(comfy: Path):
    mgr = DownloadManager(comfy)
    ok, error = mgr.rename_file("loras", "a.safetensors", "sdxl/b.safetensors")
    assert not ok and error_status(error) == 409
    assert (comfy / "models" / "loras" / "sdxl" / "b.safetensors").read_bytes() == b"b" * 20
    ok, error = mgr.rename_file("loras", "missing.safetensors", "x.safetensors")
    assert not ok and error_status(error) == 404
    ok, error = mgr.rename_file("loras", "sdxl", "sdxl2")  # directories are not renamed
    assert not ok and error_status(error) == 404


def test_rename_case_only(comfy: Path):
    mgr = DownloadManager(comfy)
    ok, error = mgr.rename_file("loras", "a.safetensors", "A.safetensors")
    assert ok, error
    assert [p.name for p in (comfy / "models" / "loras").glob("*.safetensors")] == ["A.safetensors"]


@pytest.mark.parametrize(
    "new_path",
    ["../../main.py", "..\\escape.bin", "/etc/x", "C:/x", "", ".hidden2", "sdxl/.x", "bad?.bin", " x.bin", "x.bin "],
)
def test_rename_rejects_bad_targets(comfy: Path, new_path: str):
    ok, error = DownloadManager(comfy).rename_file("loras", "a.safetensors", new_path)
    assert not ok and error_status(error) == 400, error
    assert (comfy / "models" / "loras" / "a.safetensors").exists()
    assert (comfy / "main.py").read_text() == "# outside models"


def test_rename_rejects_traversal_source(comfy: Path):
    ok, error = DownloadManager(comfy).rename_file("loras", "../../main.py", "stolen.py")
    assert not ok and error_status(error) == 400
    assert (comfy / "main.py").exists()


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
        ok, error = mgr.rename_file("loras", "a.safetensors", "moved.safetensors")
        assert not ok and error_status(error) == 409
        ok, error = mgr.rename_file("loras", "sdxl/b.safetensors", "a.safetensors")  # onto the upload target
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


def test_chunked_upload_happy_path(comfy: Path):
    mgr = DownloadManager(comfy)

    async def scenario():
        start, error = mgr.start_upload("loras", "chunked.safetensors", 10)
        assert error is None
        uid = start["upload_id"]
        data, error = await mgr.upload_chunk(uid, 0, _chunks(b"0123"), 4)
        assert error is None and data == {"upload_id": uid, "received": 4, "total": 10, "done": False}
        # The in-progress file is marked active and can't be deleted.
        listing, _ = mgr.list_files("loras")
        assert {f["path"] for f in listing["files"] if f["active"]} == {"chunked.safetensors.tmp"}
        data, error = await mgr.upload_chunk(uid, 4, _chunks(b"456", b"789"), 6)
        assert error is None and data["done"] and data["size"] == 10
        return uid

    uid = asyncio.run(scenario())
    folder = comfy / "models" / "loras"
    assert (folder / "chunked.safetensors").read_bytes() == b"0123456789"
    assert not (folder / "chunked.safetensors.tmp").exists()
    assert mgr.get_upload(uid)[1] is not None
    assert not mgr._uploads


def test_chunked_upload_empty_file(comfy: Path):
    mgr = DownloadManager(comfy)

    async def scenario():
        start, _ = mgr.start_upload("loras", "empty.bin", 0)
        return await mgr.upload_chunk(start["upload_id"], 0, _chunks())

    data, error = asyncio.run(scenario())
    assert error is None and data["done"]
    assert (comfy / "models" / "loras" / "empty.bin").read_bytes() == b""


def test_chunked_upload_failed_chunk_rolls_back_and_resumes(comfy: Path):
    mgr = DownloadManager(comfy)

    async def broken():
        yield b"XX"
        raise ConnectionResetError("dropped")

    async def scenario():
        start, _ = mgr.start_upload("loras", "resume.bin", 6)
        uid = start["upload_id"]
        await mgr.upload_chunk(uid, 0, _chunks(b"abc"), 3)
        data, error = await mgr.upload_chunk(uid, 3, broken(), 3)
        assert error and "dropped" in error and data == {"received": 3}
        # Partial bytes were rolled back.
        assert (comfy / "models" / "loras" / "resume.bin.tmp").stat().st_size == 3
        # Wrong offset (e.g. client thinks the lost chunk landed) is rejected with the real offset.
        data, error = await mgr.upload_chunk(uid, 6, _chunks(b"zzz"), 3)
        assert error_status(error) == 409 and data == {"received": 3}
        # Truncated request body is rejected and rolled back too.
        data, error = await mgr.upload_chunk(uid, 3, _chunks(b"d"), 3)
        assert error and "incomplete" in error and data == {"received": 3}
        return await mgr.upload_chunk(uid, 3, _chunks(b"def"), 3)

    data, error = asyncio.run(scenario())
    assert error is None and data["done"]
    assert (comfy / "models" / "loras" / "resume.bin").read_bytes() == b"abcdef"


def test_chunked_upload_rejects_overflow(comfy: Path):
    mgr = DownloadManager(comfy)

    async def scenario():
        start, _ = mgr.start_upload("loras", "small.bin", 2)
        return await mgr.upload_chunk(start["upload_id"], 0, _chunks(b"abc"))

    data, error = asyncio.run(scenario())
    assert error and "exceeds" in error and data == {"received": 0}


def test_chunked_upload_validation(comfy: Path):
    mgr = DownloadManager(comfy)
    _, error = mgr.start_upload("loras", "a.safetensors", 5)
    assert error_status(error) == 409  # exists, no overwrite
    _, error = mgr.start_upload("loras", "x.bin", -1)
    assert error_status(error) == 400
    _, error = mgr.start_upload("loras", "x.bin", "10")
    assert error_status(error) == 400
    _, error = mgr.start_upload("..", "x.bin", 1)
    assert error_status(error) == 400
    _, error = mgr.start_upload("loras", "x.bin", 1 << 62)
    assert error_status(error) == 507
    start, error = mgr.start_upload("loras", "x.bin", 5)
    assert error is None
    _, error = mgr.start_upload("loras", "x.bin", 5)
    assert error_status(error) == 409  # already in progress
    data, error = asyncio.run(mgr.upload_chunk("nope", 0, _chunks(b"x")))
    assert error_status(error) == 404 and data is None


def test_chunked_upload_abort_and_expiry(comfy: Path, monkeypatch):
    mgr = DownloadManager(comfy)
    folder = comfy / "models" / "loras"

    start, _ = mgr.start_upload("loras", "abort.bin", 5)
    ok, _ = mgr.abort_upload(start["upload_id"])
    assert ok and not (folder / "abort.bin.tmp").exists() and not mgr._uploads

    start, _ = mgr.start_upload("loras", "stale.bin", 5)
    assert (folder / "stale.bin.tmp").exists()
    monkeypatch.setattr(download_manager, "UPLOAD_SESSION_TTL", -1)
    ok, error = mgr.delete_file("loras", "stale.bin.tmp")  # expiry runs first, so the temp is already gone
    assert not ok and error_status(error) == 404
    assert mgr.get_upload(start["upload_id"])[1] is not None and not mgr._uploads


def test_chunked_upload_stalled_chunk_takeover(comfy: Path, monkeypatch):
    mgr = DownloadManager(comfy)

    async def scenario():
        start, _ = mgr.start_upload("loras", "stall.bin", 6)
        uid = start["upload_id"]
        gate = asyncio.Event()

        async def stalled():
            yield b"ab"
            await gate.wait()
            raise ConnectionResetError("finally noticed the dead connection")

        old = asyncio.create_task(mgr.upload_chunk(uid, 0, stalled(), 6))
        await asyncio.sleep(0.1)
        _, error = await mgr.upload_chunk(uid, 0, _chunks(b"abcdef"), 6)
        assert error_status(error) == 409  # still busy and not yet stalled

        monkeypatch.setattr(download_manager, "UPLOAD_CHUNK_STALL_TIMEOUT", 0)
        data, error = await mgr.upload_chunk(uid, 0, _chunks(b"abcdef"), 6)
        assert error is None and data["done"]

        gate.set()
        _, error = await old  # The superseded request must not roll back the new data.
        assert "superseded" in error or "dead connection" in error

    asyncio.run(scenario())
    assert (comfy / "models" / "loras" / "stall.bin").read_bytes() == b"abcdef"
