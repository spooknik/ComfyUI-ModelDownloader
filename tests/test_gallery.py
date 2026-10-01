"""Gallery feature: path safety, listing, thumbnails, bulk delete and streamed zip downloads (through the HTTP API)."""

import asyncio
import contextlib
import io
import os
import sys
import threading
import time
import types
import zipfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from spooktools import gallery as gallery_mod
from spooktools.download_manager import DownloadManager
from spooktools.gallery import Gallery, GalleryError, safe_relative_path
from spooktools.routes import API_PREFIX, LEGACY_API_PREFIX, register_routes

API = API_PREFIX
G = f"{API}/gallery"


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch) -> types.SimpleNamespace:
    """ComfyUI's folder_paths faked with output/input/temp dirs that are NOT under the base dir (like
    --output-directory), plus a secret file next to them that must stay unreachable."""
    ns = types.SimpleNamespace(
        base=tmp_path / "comfy",
        output=tmp_path / "data" / "out",
        input=tmp_path / "data" / "in",
        temp=tmp_path / "data" / "temp",
        secret=tmp_path / "data" / "secret.txt",
    )
    for d in (ns.base, ns.output, ns.input, ns.temp):
        d.mkdir(parents=True)
    ns.secret.write_text("top secret")
    fake = types.ModuleType("folder_paths")
    fake.base_path = str(ns.base)
    fake.get_output_directory = lambda: str(ns.output)
    fake.get_input_directory = lambda: str(ns.input)
    fake.get_temp_directory = lambda: str(ns.temp)
    monkeypatch.setitem(sys.modules, "folder_paths", fake)
    return ns


@pytest.fixture
def make_client(dirs):
    @contextlib.asynccontextmanager
    async def factory():
        app = web.Application()
        register_routes(app, DownloadManager(comfyui_base=dirs.base))
        async with TestClient(TestServer(app)) as client:
            yield client

    return factory


def run(coro):
    return asyncio.run(coro)


def write(path: Path, data: bytes = b"x", mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def save_image(path: Path, image: Image.Image, fmt: str | None = None, **kwargs) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, fmt, **kwargs)
    return path


def png_bytes(color=(255, 0, 0), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


# ---- path safety ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "..",
        "../secret.txt",
        "a/../../secret.txt",
        "a\\..\\..\\secret.txt",
        "/etc/passwd",
        "\\\\server\\share\\x.png",
        "C:/Windows/win.ini",
        "C:secret.txt",
        "img.png:stream",
        ".hidden.png",
        "sub/.git/config",
        " ../secret.txt",
        "a/ ./b.png",
        "a\x00b.png",
        None,
        5,
        ["a.png"],
    ],
)
def test_safe_relative_path_rejects(bad):
    with pytest.raises(GalleryError):
        safe_relative_path(bad)


def test_safe_relative_path_accepts():
    assert safe_relative_path("a.png").as_posix() == "a.png"
    assert safe_relative_path("sub\\deeper/b.png").as_posix() == "sub/deeper/b.png"
    assert safe_relative_path("./sub//c.png").as_posix() == "sub/c.png"
    assert safe_relative_path("", allow_empty=True).parts == ()
    assert safe_relative_path("my file (1).png").as_posix() == "my file (1).png"


def test_roots_come_from_folder_paths_with_fallback(dirs, monkeypatch, tmp_path):
    g = Gallery(dirs.base)
    assert g.root_directory("output") == dirs.output
    assert g.root_directory("input") == dirs.input
    assert g.thumb_dir == dirs.temp / "spooktools-thumbs"
    with pytest.raises(GalleryError):
        g.root_directory("models")
    monkeypatch.setitem(sys.modules, "folder_paths", None)  # Not importable (no ComfyUI).
    assert g.root_directory("output") == dirs.base / "output"
    assert g.root_directory("input") == dirs.base / "input"
    assert Gallery(dirs.base, roots={"output": tmp_path}).root_directory("output") == tmp_path


def test_http_rejects_unknown_root_and_traversal(dirs, make_client):
    async def scenario():
        async with make_client() as client:
            for prefix in (API_PREFIX, LEGACY_API_PREFIX):
                resp = await client.get(f"{prefix}/gallery/list", params={"root": "models"})
                assert resp.status == 400 and "root" in (await resp.json())["error"].lower()
            for params in (
                {"root": "temp", "path": "x.png"},
                {"root": "output", "path": "../secret.txt"},
                {"root": "output", "path": str(dirs.secret)},
                {"root": "output", "path": "..\\secret.txt"},
            ):
                for endpoint in ("thumb", "file"):
                    resp = await client.get(f"{G}/{endpoint}", params=params)
                    assert resp.status == 400, (endpoint, params)
            resp = await client.get(f"{G}/list", params={"root": "output", "subfolder": ".."})
            assert resp.status == 400
            resp = await client.post(f"{G}/delete", json={"root": "output", "paths": ["../secret.txt"]})
            assert resp.status == 200
            body = await resp.json()
            assert body == {"deleted": [], "failed": [{"path": "../secret.txt", "error": "Invalid path"}]}
            resp = await client.post(f"{G}/delete", json={"root": "../data", "paths": ["secret.txt"]})
            assert resp.status == 400
            resp = await client.post(f"{G}/zip", json={"root": "output", "paths": ["../secret.txt"]})
            assert resp.status == 400
            resp = await client.post(f"{G}/zip", data=b"not json")
            assert resp.status == 400
            resp = await client.get(f"{G}/list", params={"root": "output", "limit": "lots"})
            assert resp.status == 400
            resp = await client.get(f"{G}/list", params={"root": "output", "sort": "random"})
            assert resp.status == 400

    run(scenario())
    assert dirs.secret.read_text() == "top secret"


# ---- listing --------------------------------------------------------------------------------------------------


@pytest.fixture
def populated(dirs):
    out = dirs.output
    write(out / "a.png", b"a" * 10, mtime=1_000_000)
    write(out / "B.jpg", b"b" * 30, mtime=1_000_300)
    write(out / "c.webp", b"c" * 20, mtime=1_000_100)
    write(out / "clip.mp4", b"v" * 50, mtime=1_000_200)
    write(out / "workflow.json", b"{}", mtime=1_000_050)
    write(out / ".hidden.png", b"h")
    write(out / "sub" / "d.png", b"d" * 5, mtime=1_000_400)
    write(out / "sub" / "deeper" / "e.gif", b"e" * 7, mtime=1_000_500)
    write(out / ".cache" / "f.png", b"f")
    write(out / "empty" / ".keep", b"")
    return dirs


def test_list_non_recursive_and_subfolders(populated, make_client):
    async def scenario():
        async with make_client() as client:
            resp = await client.get(f"{G}/list", params={"root": "output"})
            assert resp.status == 200
            data = await resp.json()
            assert data["root"] == "output" and data["subfolder"] == "" and data["offset"] == 0
            assert Path(data["directory"]) == populated.output
            assert data["subfolders"] == ["empty", "sub"]  # Hidden .cache skipped.
            # Newest first by default; hidden files skipped; non-recursive.
            assert [i["path"] for i in data["items"]] == ["B.jpg", "clip.mp4", "c.webp", "workflow.json", "a.png"]
            assert data["total"] == 5 and data["total_size"] == 10 + 30 + 20 + 50 + 2
            kinds = {i["path"]: i["kind"] for i in data["items"]}
            assert kinds == {
                "B.jpg": "image",
                "clip.mp4": "video",
                "c.webp": "image",
                "workflow.json": "other",
                "a.png": "image",
            }
            first = next(i for i in data["items"] if i["path"] == "a.png")
            assert first == {"path": "a.png", "name": "a.png", "size": 10, "modified": 1_000_000, "kind": "image"}

            resp = await client.get(f"{G}/list", params={"root": "output", "subfolder": "sub"})
            data = await resp.json()
            assert data["subfolder"] == "sub" and data["subfolders"] == ["deeper"]
            assert [i["path"] for i in data["items"]] == ["sub/d.png"]

            resp = await client.get(f"{G}/list", params={"root": "output", "subfolder": "nope"})
            assert resp.status == 404
            resp = await client.get(f"{G}/list", params={"root": "output", "subfolder": "a.png"})
            assert resp.status == 404

    run(scenario())


def test_list_recursive_sort_filter_paging(populated, make_client):
    async def names(client, **params):
        resp = await client.get(f"{G}/list", params={"root": "output", "recursive": "1", **params})
        assert resp.status == 200, await resp.text()
        data = await resp.json()
        return [i["path"] for i in data["items"]], data

    async def scenario():
        async with make_client() as client:
            got, data = await names(client)
            assert got == ["sub/deeper/e.gif", "sub/d.png", "B.jpg", "clip.mp4", "c.webp", "workflow.json", "a.png"]
            assert data["total"] == 7 and data["subfolders"] == ["empty", "sub"]
            got, _ = await names(client, sort="oldest")
            assert got == ["a.png", "workflow.json", "c.webp", "clip.mp4", "B.jpg", "sub/d.png", "sub/deeper/e.gif"]
            got, _ = await names(client, sort="name")
            assert got == ["a.png", "B.jpg", "c.webp", "clip.mp4", "sub/d.png", "sub/deeper/e.gif", "workflow.json"]
            got, _ = await names(client, sort="size")
            assert got == ["clip.mp4", "B.jpg", "c.webp", "a.png", "sub/deeper/e.gif", "sub/d.png", "workflow.json"]

            got, data = await names(client, kind="image", sort="name")
            assert got == ["a.png", "B.jpg", "c.webp", "sub/d.png", "sub/deeper/e.gif"] and data["total"] == 5
            got, _ = await names(client, kind="video")
            assert got == ["clip.mp4"]
            got, _ = await names(client, kind="other")
            assert got == ["workflow.json"]

            got, data = await names(client, q="SUB", sort="name")
            assert got == ["sub/d.png", "sub/deeper/e.gif"] and data["total"] == 2 and data["total_size"] == 12
            got, _ = await names(client, q="b.j")
            assert got == ["B.jpg"]
            # The search matches the path below the listed subfolder, not the subfolder name itself.
            got, _ = await names(client, subfolder="sub", q="sub")
            assert got == []

            got, data = await names(client, sort="name", offset="2", limit="3")
            assert got == ["c.webp", "clip.mp4", "sub/d.png"] and data["total"] == 7 and data["offset"] == 2
            got, data = await names(client, sort="name", offset="6", limit="3")
            assert got == ["workflow.json"]
            got, data = await names(client, offset="50")
            assert got == [] and data["total"] == 7
            got, data = await names(client, limit="0")
            assert got == [] and data["total"] == 7 and data["total_size"] == 124

            params = {"root": "output", "recursive": "1", "paths_only": "1", "sort": "name", "kind": "image"}
            resp = await client.get(f"{G}/list", params=params)
            data = await resp.json()
            assert "items" not in data
            assert data["paths"] == ["a.png", "B.jpg", "c.webp", "sub/d.png", "sub/deeper/e.gif"]
            assert data["sizes"] == [10, 30, 20, 5, 7]

            # An input root that does not exist yet is just empty.
            populated.input.rmdir()
            resp = await client.get(f"{G}/list", params={"root": "input", "recursive": "1"})
            data = await resp.json()
            assert resp.status == 200 and data["total"] == 0 and data["items"] == [] and data["subfolders"] == []

    run(scenario())


def test_listing_cache_sees_new_and_deleted_files(dirs):
    g = Gallery(dirs.base)
    write(dirs.output / "one.png")
    assert g.list_files("output")["total"] == 1
    # A new file changes the directory mtime, which invalidates the cached scan.
    time.sleep(0.02)
    write(dirs.output / "two.png")
    assert g.list_files("output")["total"] == 2
    write(dirs.output / "sub" / "three.png")
    assert g.list_files("output", recursive=True)["total"] == 3
    time.sleep(0.02)
    write(dirs.output / "sub" / "four.png")
    assert g.list_files("output", recursive=True)["total"] == 4
    g.delete_files("output", ["sub/four.png"])
    assert g.list_files("output", recursive=True)["total"] == 3


def test_listing_follows_symlinked_folders_without_cycles(dirs, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    write(elsewhere / "linked.png")
    try:
        os.symlink(elsewhere, dirs.output / "link", target_is_directory=True)
        os.symlink(dirs.output, elsewhere / "loop", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    write(dirs.output / "top.png")
    data = Gallery(dirs.base).list_files("output", recursive=True, sort="name")
    assert [i["path"] for i in data["items"]] == ["link/linked.png", "top.png"]
    assert data["subfolders"] == ["link"]
    assert Gallery(dirs.base).existing_file("output", "link/linked.png")[0] == dirs.output / "link" / "linked.png"


# ---- thumbnails -----------------------------------------------------------------------------------------------


def thumb_image(body: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(body))
    image.load()
    return image.convert("RGB")


def test_thumbnails_formats(dirs, make_client):
    out = dirs.output
    save_image(out / "red.png", Image.new("RGB", (800, 400), (255, 0, 0)))
    save_image(out / "photo.jpg", Image.new("RGB", (1200, 900), (0, 0, 255)), quality=95)
    # EXIF orientation 6 (rotate 90): a 300x100 landscape is displayed as a 100x300 portrait.
    exif = Image.Exif()
    exif[0x0112] = 6
    save_image(out / "rotated.jpg", Image.new("RGB", (300, 100), (0, 200, 0)), exif=exif.tobytes())
    frames = [Image.new("RGB", (120, 120), c) for c in ((0, 255, 0), (255, 0, 255), (0, 0, 255))]
    frames[0].save(out / "anim.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    save_image(out / "clear.png", Image.new("RGBA", (64, 64), (255, 255, 255, 0)))
    save_image(out / "gray16.png", Image.new("I;16", (64, 64), 32768))
    save_image(out / "cmyk.jpg", Image.new("CMYK", (64, 64), (0, 0, 0, 0)))
    save_image(out / "pal.png", Image.new("P", (64, 64), 3))
    save_image(out / "pic.bmp", Image.new("RGB", (64, 64), (10, 20, 30)))
    save_image(out / "pic.webp", Image.new("RGB", (64, 64), (200, 100, 0)))
    write(out / "broken.png", b"\x89PNG\r\n\x1a\nthis is not really a png")
    write(out / "truncated.jpg", (out / "photo.jpg").read_bytes()[:300])
    write(out / "clip.mp4", b"\x00" * 100)

    async def thumb(client, path, size=256, expect=200):
        resp = await client.get(f"{G}/thumb", params={"root": "output", "path": path, "size": str(size), "v": "1"})
        assert resp.status == expect, (path, resp.status, await resp.text())
        return resp, await resp.read()

    def near(a, b, tol=12):
        return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))

    async def scenario():
        async with make_client() as client:
            resp, body = await thumb(client, "red.png", size=200)
            assert resp.headers["Content-Type"] == "image/webp"
            assert "immutable" in resp.headers["Cache-Control"] and resp.headers["ETag"]
            image = thumb_image(body)
            assert image.size == (256, 128)  # 200 rounds up to the 256 bucket; aspect ratio kept.
            assert near(image.getpixel((10, 10)), (255, 0, 0))

            _, body = await thumb(client, "photo.jpg")
            image = thumb_image(body)
            assert image.size == (256, 192) and near(image.getpixel((5, 5)), (0, 0, 255))

            _, body = await thumb(client, "rotated.jpg")
            assert thumb_image(body).size == (85, 256)  # Portrait after applying the EXIF rotation.

            _, body = await thumb(client, "anim.gif")
            assert near(thumb_image(body).getpixel((60, 60)), (0, 255, 0))  # First frame.

            _, body = await thumb(client, "clear.png")
            assert near(thumb_image(body).getpixel((30, 30)), gallery_mod.THUMB_BACKGROUND, tol=4)

            _, body = await thumb(client, "gray16.png")
            assert near(thumb_image(body).getpixel((30, 30)), (128, 128, 128), tol=3)

            _, body = await thumb(client, "cmyk.jpg")
            assert near(thumb_image(body).getpixel((30, 30)), (255, 255, 255), tol=6)

            for name in ("pal.png", "pic.bmp", "pic.webp"):
                await thumb(client, name)

            for name in ("broken.png", "truncated.jpg", "clip.mp4"):
                resp, _ = await thumb(client, name, expect=415)
            resp, _ = await thumb(client, "missing.png", expect=404)

    run(scenario())


def test_thumbnail_cache_and_etag(dirs, make_client, monkeypatch):
    save_image(dirs.output / "a.png", Image.new("RGB", (300, 300), (1, 2, 3)))
    calls = []
    real = gallery_mod.render_thumbnail_bytes

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(gallery_mod, "render_thumbnail_bytes", counting)

    async def scenario():
        async with make_client() as client:
            params = {"root": "output", "path": "a.png", "size": "256"}
            resp = await client.get(f"{G}/thumb", params=params)
            first = await resp.read()
            etag = resp.headers["ETag"]
            assert resp.status == 200 and resp.headers["Cache-Control"] == "private, no-cache"
            assert len(calls) == 1
            assert len(list((dirs.temp / "spooktools-thumbs").rglob("*.webp"))) == 1

            resp = await client.get(f"{G}/thumb", params=params)
            assert resp.status == 200 and await resp.read() == first and resp.headers["ETag"] == etag
            assert len(calls) == 1  # Served from the disk cache.

            resp = await client.get(f"{G}/thumb", params=params, headers={"If-None-Match": etag})
            assert resp.status == 304

            # Changing the file changes the cache key.
            time.sleep(0.02)
            save_image(dirs.output / "a.png", Image.new("RGB", (300, 300), (9, 9, 9)))
            os.utime(dirs.output / "a.png", (time.time() + 5, time.time() + 5))
            resp = await client.get(f"{G}/thumb", params=params, headers={"If-None-Match": etag})
            assert resp.status == 200 and resp.headers["ETag"] != etag and len(calls) == 2

            # A corrupt file is only tried once.
            write(dirs.output / "bad.png", b"garbage")
            for _ in range(2):
                resp = await client.get(f"{G}/thumb", params={"root": "output", "path": "bad.png"})
                assert resp.status == 415
            assert len(calls) == 3

            # Deleting a file removes the cached thumbnails of its current version (the one of the overwritten
            # version stays until ComfyUI empties its temp dir on the next start).
            cached = lambda: len(list((dirs.temp / "spooktools-thumbs").rglob("*.webp")))  # noqa: E731
            assert cached() == 2
            resp = await client.post(f"{G}/delete", json={"root": "output", "paths": ["a.png"]})
            assert (await resp.json())["deleted"] == ["a.png"]
            assert cached() == 1

    run(scenario())


def test_thumbnail_concurrency_is_bounded(dirs, make_client, monkeypatch):
    for i in range(12):
        save_image(dirs.output / f"img{i}.png", Image.new("RGB", (64, 64), (i, i, i)))
    active = 0
    peak = 0
    lock = threading.Lock()
    real = gallery_mod.render_thumbnail_bytes

    def slow(*args, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        try:
            return real(*args, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(gallery_mod, "render_thumbnail_bytes", slow)

    async def scenario():
        async with make_client() as client:
            responses = await asyncio.gather(
                *(client.get(f"{G}/thumb", params={"root": "output", "path": f"img{i}.png"}) for i in range(12))
            )
            assert [r.status for r in responses] == [200] * 12

    run(scenario())
    assert 1 <= peak <= gallery_mod.THUMB_WORKERS


# ---- original files -------------------------------------------------------------------------------------------


def test_file_endpoint(dirs, make_client):
    write(dirs.output / "sub" / "a.png", png_bytes())
    write(dirs.output / "clip.mp4", b"\x00\x01" * 500)
    write(dirs.output / "page.html", b"<script>alert(1)</script>")
    write(dirs.input / "in.webp", b"webp-bytes")

    async def scenario():
        async with make_client() as client:
            resp = await client.get(f"{G}/file", params={"root": "output", "path": "sub/a.png"})
            assert resp.status == 200 and await resp.read() == png_bytes()
            assert resp.headers["Content-Type"] == "image/png"
            assert resp.headers["Content-Disposition"].startswith("inline;")
            assert resp.headers["X-Content-Type-Options"] == "nosniff"

            resp = await client.get(
                f"{G}/file", params={"root": "output", "path": "clip.mp4"}, headers={"Range": "bytes=0-9"}
            )
            assert resp.status == 206 and resp.headers["Content-Type"] == "video/mp4" and len(await resp.read()) == 10

            resp = await client.get(f"{G}/file", params={"root": "output", "path": "page.html"})
            assert resp.headers["Content-Type"] == "application/octet-stream"
            assert resp.headers["Content-Disposition"].startswith("attachment;")

            resp = await client.get(f"{G}/file", params={"root": "input", "path": "in.webp"})
            assert resp.status == 200 and await resp.read() == b"webp-bytes"

            resp = await client.get(f"{G}/file", params={"root": "output", "path": "sub"})
            assert resp.status == 404
            resp = await client.get(f"{G}/file", params={"root": "output", "path": "nope.png"})
            assert resp.status == 404

    run(scenario())


# ---- delete ---------------------------------------------------------------------------------------------------


def test_delete(dirs, make_client, monkeypatch):
    write(dirs.input / "a.png")
    write(dirs.input / "sub" / "b.png")
    write(dirs.input / "sub" / "c.png")
    (dirs.input / "folder").mkdir()

    async def scenario():
        async with make_client() as client:
            resp = await client.post(
                f"{G}/delete",
                json={"root": "input", "paths": ["a.png", "sub\\b.png", "missing.png", "folder", "../x", "a.png"]},
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["deleted"] == ["a.png", "sub\\b.png"]
            assert body["failed"] == [
                {"path": "missing.png", "error": "File not found"},
                {"path": "folder", "error": "Not a file"},
                {"path": "../x", "error": "Invalid path"},
            ]
            assert not (dirs.input / "a.png").exists() and not (dirs.input / "sub" / "b.png").exists()
            assert (dirs.input / "sub" / "c.png").exists() and (dirs.input / "folder").is_dir()

            resp = await client.get(f"{G}/list", params={"root": "input", "recursive": "1"})
            assert [i["path"] for i in (await resp.json())["items"]] == ["sub/c.png"]

            for bad in (
                {"root": "input", "paths": "a.png"},
                {"root": "input", "paths": []},
                {"root": "input", "paths": [1]},
                {"root": "input"},
                ["a.png"],
            ):
                resp = await client.post(f"{G}/delete", json=bad)
                assert resp.status == 400, bad

            monkeypatch.setattr(gallery_mod, "MAX_DELETE_BATCH", 3)
            resp = await client.post(f"{G}/delete", json={"root": "input", "paths": ["a", "b", "c", "d"]})
            assert resp.status == 400 and "max 3" in (await resp.json())["error"]
            assert (dirs.input / "sub" / "c.png").exists()

    run(scenario())


# ---- zip ------------------------------------------------------------------------------------------------------


@pytest.fixture
def zip_files(dirs):
    files = {
        "a.png": png_bytes((1, 2, 3)),
        "sub/b.png": png_bytes((4, 5, 6)),
        "sub/deeper/c.bin": os.urandom(3 << 20),
        "other/b.png": b"same name, different folder",
    }
    for rel, data in files.items():
        write(dirs.output / rel, data)
    return files


async def fetch_zip(client, prefix, root, paths):
    resp = await client.post(f"{prefix}/gallery/zip", json={"root": root, "paths": paths})
    assert resp.status == 201, await resp.text()
    meta = await resp.json()
    resp = await client.get(f"{prefix}/gallery/zip/{meta['token']}")
    assert resp.status == 200
    return meta, resp, await resp.read()


@pytest.mark.parametrize("prefix", [API_PREFIX, LEGACY_API_PREFIX])
def test_zip_roundtrip(dirs, zip_files, make_client, prefix):
    async def scenario():
        async with make_client() as client:
            meta, resp, body = await fetch_zip(client, prefix, "output", list(zip_files) + ["gone.png"])
            assert meta["count"] == 4 and meta["missing"] == ["gone.png"]
            assert meta["total_size"] == sum(len(d) for d in zip_files.values())
            assert meta["filename"].startswith("spooktools-output-") and meta["filename"].endswith(".zip")
            assert resp.headers["Content-Type"] == "application/zip"
            assert resp.headers["Content-Disposition"].startswith(f'attachment; filename="{meta["filename"]}"')
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                assert archive.testzip() is None
                assert sorted(archive.namelist()) == sorted(zip_files)
                for rel, data in zip_files.items():
                    info = archive.getinfo(rel)
                    assert info.compress_type == zipfile.ZIP_STORED
                    assert archive.read(rel) == data

    run(scenario())


def test_zip_token_rules(dirs, zip_files, make_client, monkeypatch):
    async def scenario():
        async with make_client() as client:
            resp = await client.get(f"{G}/zip/not-a-token")
            assert resp.status == 404
            resp = await client.post(f"{G}/zip", json={"root": "output", "paths": ["gone.png"]})
            assert resp.status == 404
            resp = await client.post(f"{G}/zip", json={"root": "output", "paths": []})
            assert resp.status == 400

            # A few uses, then the token is gone. HEAD is not routed, so it cannot use one up.
            resp = await client.post(f"{G}/zip", json={"root": "output", "paths": ["a.png"]})
            token = (await resp.json())["token"]
            resp = await client.head(f"{G}/zip/{token}")
            assert resp.status == 405
            for _ in range(gallery_mod.ZIP_TOKEN_USES):
                resp = await client.get(f"{G}/zip/{token}")
                assert resp.status == 200 and zipfile.ZipFile(io.BytesIO(await resp.read())).namelist() == ["a.png"]
            resp = await client.get(f"{G}/zip/{token}")
            assert resp.status == 404

            # Expired.
            resp = await client.post(f"{G}/zip", json={"root": "output", "paths": ["a.png"]})
            token = (await resp.json())["token"]
            real_monotonic = time.monotonic
            monkeypatch.setattr(gallery_mod.time, "monotonic", lambda: real_monotonic() + gallery_mod.ZIP_TOKEN_TTL + 1)
            resp = await client.get(f"{G}/zip/{token}")
            assert resp.status == 404

    run(scenario())


def test_zip_skips_files_deleted_after_prepare(dirs, zip_files, make_client):
    async def scenario():
        async with make_client() as client:
            resp = await client.post(f"{G}/zip", json={"root": "output", "paths": list(zip_files)})
            token = (await resp.json())["token"]
            (dirs.output / "sub" / "b.png").unlink()
            resp = await client.get(f"{G}/zip/{token}")
            body = await resp.read()
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                assert archive.testzip() is None
                assert sorted(archive.namelist()) == sorted(set(zip_files) - {"sub/b.png"})
                assert archive.read("other/b.png") == zip_files["other/b.png"]

    run(scenario())


def test_zip64_entries_and_offsets(dirs, zip_files, make_client, monkeypatch):
    """Lower zipfile's zip64 threshold so the >4 GB code paths run on small files."""
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1 << 20)

    async def scenario():
        async with make_client() as client:
            _, _, body = await fetch_zip(client, API, "output", list(zip_files))
            monkeypatch.undo()
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                assert archive.testzip() is None
                for rel, data in zip_files.items():
                    assert archive.read(rel) == data
            assert b"PK\x06\x06" in body  # zip64 end of central directory record

    run(scenario())


def test_zip_worker_stops_when_client_disconnects(dirs, make_client):
    for i in range(30):
        write(dirs.output / f"big{i}.bin", os.urandom(1 << 20))

    def zip_threads():
        return [t for t in threading.enumerate() if t.name == "spooktools-zip"]

    async def scenario():
        async with make_client() as client:
            resp = await client.post(f"{G}/zip", json={"root": "output", "paths": [f"big{i}.bin" for i in range(30)]})
            token = (await resp.json())["token"]
            resp = await client.get(f"{G}/zip/{token}")
            assert resp.status == 200
            await resp.content.readexactly(64 << 10)
            assert zip_threads(), "zip thread should still be running (backpressure)"
            resp.close()  # Drop the connection mid-download.
            for _ in range(60):
                if not zip_threads():
                    break
                await asyncio.sleep(0.05)
            assert not zip_threads(), "zip thread kept running after the client went away"

    run(scenario())


def test_chunk_pipe_backpressure_bounds_memory(dirs, zip_files):
    """The writer blocks once ZIP_QUEUE_CHUNKS chunks are waiting, and exits promptly on cancel."""

    async def scenario():
        g = Gallery(dirs.base)
        job, _ = g.prepare_zip("output", list(zip_files))
        pipe = gallery_mod.ChunkPipe(asyncio.get_running_loop(), max_chunks=2)
        worker = threading.Thread(target=Gallery.write_zip, args=(job, pipe), daemon=True)
        worker.start()
        await asyncio.sleep(0.3)  # Nobody reads: the writer must be blocked, not buffering everything.
        assert worker.is_alive()
        assert pipe._queue.qsize() <= 2
        pipe.cancel()
        await asyncio.get_running_loop().run_in_executor(None, worker.join, 2)
        assert not worker.is_alive()

    run(scenario())
