"""Bulk image manager for ComfyUI's output/ and input/ folders: listing, thumbnails, bulk delete and zip streaming.

No HTTP here (see ``routes_gallery.py``). Every path a client sends is relative to one of the two roots and is
checked lexically, like the model file manager, so symlinked subfolders keep working while ``..``, absolute
paths and drive letters are rejected. Blocking work (directory scans, Pillow, file I/O) is meant to run in
executor threads; the zip writer runs in its own thread and feeds an asyncio consumer through ``ChunkPipe``.
"""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import dataclasses
import hashlib
import logging
import os
import secrets
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

ROOTS = ("output", "input")
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".mkv", ".m4v"})
KINDS = ("all", "image", "video", "other")
SORTS = ("newest", "oldest", "name", "size")

DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 1000
MAX_DELETE_BATCH = 10_000
MAX_ZIP_FILES = 100_000

# Directory scans are reused for this long while no scanned directory's mtime changed (paging, typing a search,
# "select all matching"). The TTL bounds staleness for changes a directory mtime does not show (a file
# overwritten in place) and for filesystems with coarse timestamps.
LIST_CACHE_TTL = 10.0
LIST_CACHE_ENTRIES = 8

# Thumbnail edge lengths the server renders; a requested size is rounded up to one of these so the disk cache
# holds a handful of variants per image, not one per slider position.
THUMB_SIZES = (128, 256, 384, 512, 768, 1024)
THUMB_WORKERS = 4  # Concurrent Pillow renders: a 200-tile grid must not pin every core.
THUMB_BACKGROUND = (30, 30, 30)  # Transparent pixels are flattened onto the gallery's dark tile colour.
THUMB_CACHE_VERSION = "1"  # Bump when rendering changes, so stale cached thumbnails are not served.
MAX_FAILED_THUMBS = 10_000

ZIP_TOKEN_TTL = 10 * 60
ZIP_TOKEN_USES = 3  # A browser may retry a download; a token is not meant to be a permanent link.
ZIP_READ_CHUNK = 1 << 20
ZIP_FLUSH_SIZE = 512 << 10  # Small zip header writes are batched into chunks of about this size.
ZIP_QUEUE_CHUNKS = 8  # Chunks in flight between the zip thread and the HTTP response (backpressure bound).
ZIP_POLL_SECONDS = 0.25  # How often a blocked zip thread checks whether the client went away.

CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".json": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/plain; charset=utf-8",
    ".yaml": "text/plain; charset=utf-8",
    ".yml": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
}


class GalleryError(ValueError):
    """A client error. The message maps to an HTTP status via ``download_manager.error_status``."""


class ThumbnailError(Exception):
    """The file cannot be turned into a thumbnail (corrupt, unsupported, not an image)."""


class ZipCancelled(Exception):
    """Raised inside the zip thread once the HTTP consumer has gone away."""


def _extension(name: str) -> str:
    """Lower-case extension with the dot, for a bare file name (hot path: splitext is slower over 20k names)."""
    _, dot, ext = name.rpartition(".")
    return f".{ext.lower()}" if dot else ""


def file_kind(name: str) -> str:
    ext = _extension(name)
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return "other"


def content_type_for(name: str) -> tuple[str, bool]:
    """(Content-Type, inline) for serving a file. Unknown types are offered as downloads, never rendered."""
    content_type = CONTENT_TYPES.get(_extension(name))
    if content_type is None:
        return "application/octet-stream", False
    return content_type, True


def safe_relative_path(relative: Any, *, allow_empty: bool = False) -> PurePosixPath:
    """Validate a client path relative to a root (lexically). Raises GalleryError("Invalid path").

    Rejects absolute paths, drive letters/ADS (any ``:``), NUL, ``..`` and hidden components (anything starting
    with ``.``; the listing never shows those), and components Windows would normalise to nothing (``" .."``).
    Backslashes are treated as separators.
    """
    if not isinstance(relative, str):
        raise GalleryError("Invalid path")
    text = relative.replace("\\", "/")
    rel = PurePosixPath(text)
    if rel.is_absolute() or ":" in text or "\x00" in text:
        raise GalleryError("Invalid path")
    if any(part.startswith(".") or not part.rstrip(" .") for part in rel.parts):
        raise GalleryError("Invalid path")
    if not rel.parts and not allow_empty:
        raise GalleryError("Invalid path")
    return rel


def _folder_paths_directory(root: str) -> Path | None:
    """Ask ComfyUI where a root lives (honours --base-directory, --output-directory, --input-directory)."""
    try:
        import folder_paths

        getter = getattr(folder_paths, f"get_{root}_directory")
        return Path(getter())
    except Exception:  # Not running inside ComfyUI (tests, CLI) or an unexpected folder_paths.
        return None


def thumbnail_cache_directory() -> Path:
    """``<ComfyUI temp>/spooktools-thumbs``; ComfyUI empties its temp dir on start, which bounds the cache."""
    base = _folder_paths_directory("temp") or Path(tempfile.gettempdir())
    return base / "spooktools-thumbs"


@dataclasses.dataclass(slots=True)
class GalleryFile:
    path: str  # Relative to the root, POSIX separators.
    name: str
    size: int
    modified: float
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "name": self.name, "size": self.size, "modified": self.modified, "kind": self.kind}


@dataclasses.dataclass(slots=True)
class ScanResult:
    files: list[GalleryFile]
    subfolders: list[str]
    directories: list[tuple[str, int]]  # Every scanned directory with its mtime_ns (taken before listing it).
    created: float = dataclasses.field(default_factory=time.monotonic)

    def is_fresh(self, ttl: float = LIST_CACHE_TTL) -> bool:
        if time.monotonic() - self.created > ttl:
            return False
        for directory, mtime_ns in self.directories:
            try:
                if os.stat(directory).st_mtime_ns != mtime_ns:
                    return False
            except OSError:
                return False
        return True


@dataclasses.dataclass(slots=True)
class ZipJob:
    root: str
    directory: Path
    paths: list[str]
    total_size: int
    filename: str
    expires: float
    uses_left: int = ZIP_TOKEN_USES


def scan_directory(top: Path, prefix: str, recursive: bool) -> ScanResult:
    """Files under `top` (paths prefixed with `prefix`) and the names of its immediate subfolders.

    Uses scandir (on Windows the stat data comes with the directory listing for free). Follows symlinked
    directories with a cycle guard and skips hidden entries, like the model file manager's listing.
    """
    files: list[GalleryFile] = []
    subfolders: list[str] = []
    directories: list[tuple[str, int]] = []
    seen: set[str] = set()
    stack = [(os.fspath(top), prefix, True)]
    while stack:
        directory, rel, is_top = stack.pop()
        real = os.path.realpath(directory)
        if real in seen:  # Symlink cycle (or a second link to an already listed folder).
            continue
        seen.add(real)
        try:
            # mtime before listing: a change made while listing then invalidates the cached result.
            directories.append((directory, os.stat(directory).st_mtime_ns))
            entries = os.scandir(directory)
        except OSError:
            if is_top:
                raise
            continue
        with entries:
            for entry in entries:
                name = entry.name
                if name.startswith("."):
                    continue
                try:
                    if entry.is_dir():
                        if is_top:
                            subfolders.append(name)
                        if recursive:
                            stack.append((entry.path, f"{rel}{name}/", False))
                        continue
                    if not entry.is_file():
                        continue
                    st = entry.stat()
                except OSError:  # Vanished, broken symlink, permission denied.
                    continue
                files.append(GalleryFile(rel + name, name, st.st_size, st.st_mtime, file_kind(name)))
    subfolders.sort(key=str.lower)
    return ScanResult(files, subfolders, directories)


def sort_files(files: list[GalleryFile], sort: str) -> None:
    """Sort in place. Name order is the tie-breaker for the other orders (stable sorts)."""
    files.sort(key=lambda f: (f.path.lower(), f.path))
    if sort == "newest":
        files.sort(key=lambda f: f.modified, reverse=True)
    elif sort == "oldest":
        files.sort(key=lambda f: f.modified)
    elif sort == "size":
        files.sort(key=lambda f: f.size, reverse=True)


def _zip_date_time(mtime: float) -> tuple[int, int, int, int, int, int]:
    # The zip format stores local time and cannot represent years outside 1980..2107.
    parts = time.localtime(mtime)[:6]
    if parts[0] < 1980:
        return (1980, 1, 1, 0, 0, 0)
    if parts[0] > 2107:
        return (2107, 12, 31, 23, 59, 58)
    return parts  # type: ignore[return-value]


def _to_display_mode(image: Any) -> Any:
    """Convert any Pillow mode to RGB or RGBA (alpha flattened later, after resizing)."""
    mode = image.mode
    if mode.startswith("I;16"):
        return image.convert("I").point(lambda v: v * (1 / 256)).convert("L").convert("RGB")
    if mode in ("I", "F"):
        low, high = image.getextrema()
        if high > 255 or low < 0 or (mode == "F" and high <= 1.0):
            span = (high - low) or 1
            image = image.convert("F").point(lambda v: (v - low) * (255 / span))
        return image.convert("L").convert("RGB")
    if mode == "P":
        return image.convert("RGBA" if "transparency" in image.info else "RGB")
    if mode in ("RGBA", "RGB"):
        return image
    if mode in ("LA", "La", "PA", "RGBa"):
        return image.convert("RGBA")
    if mode == "L" and "transparency" in image.info:
        return image.convert("RGBA")
    return image.convert("RGB")  # 1, L, CMYK, YCbCr, LAB, HSV, ...


def render_thumbnail_bytes(source: Path, size: int, *, webp: bool = True) -> bytes:
    """A thumbnail (longest edge `size`) as WebP (or JPEG) bytes. Raises ThumbnailError for unusable files.

    Uses the first frame of animated images, applies EXIF orientation, and flattens transparency onto the
    gallery's dark tile colour. JPEG decoding uses Pillow's draft mode, so big photos are reduced while decoding.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # Pillow ships with ComfyUI; without it there are simply no thumbnails.
        raise ThumbnailError("Pillow is not installed") from exc

    import io

    try:
        with Image.open(source) as opened:
            opened.draft("RGB", (size, size))
            image = ImageOps.exif_transpose(opened)  # A loaded copy of the first frame.
        image = _to_display_mode(image)
        image.thumbnail((size, size), Image.Resampling.BICUBIC, reducing_gap=2.0)
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, THUMB_BACKGROUND)
            background.paste(image, mask=image.getchannel("A"))
            image = background
        out = io.BytesIO()
        if webp:
            image.save(out, "WEBP", quality=80, method=3)
        else:
            image.save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue()
    except Exception as exc:  # Corrupt/truncated/unsupported data surfaces as many exception types.
        raise ThumbnailError(f"Cannot create a thumbnail: {exc}") from exc


class ChunkPipe:
    """Write-only file object for ``zipfile`` (in a worker thread) whose bytes an asyncio task reads.

    Backpressure: at most ``max_chunks`` chunks are queued; the writer blocks until the reader takes one. The
    blocked writer polls ``cancelled`` so it stops promptly once the reader is gone (client disconnected).
    """

    END = object()

    def __init__(self, loop: asyncio.AbstractEventLoop, max_chunks: int = ZIP_QUEUE_CHUNKS) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._slots = threading.Semaphore(max_chunks)
        self._buffer = bytearray()
        self.cancelled = threading.Event()

    # -- writer side (zip thread) ---------------------------------------------------------------------------

    def write(self, data: bytes) -> int:
        if self.cancelled.is_set():
            raise ZipCancelled
        self._buffer += data
        if len(self._buffer) >= ZIP_FLUSH_SIZE:
            self._send(bytes(self._buffer))
            self._buffer.clear()
        return len(data)

    def flush(self) -> None:
        """zipfile calls this; buffered bytes are sent once the batch is big enough or at ``finish()``."""

    def _send(self, chunk: bytes) -> None:
        while not self._slots.acquire(timeout=ZIP_POLL_SECONDS):
            if self.cancelled.is_set():
                raise ZipCancelled
        if self.cancelled.is_set():
            raise ZipCancelled
        self._put(chunk)

    def _put(self, item: Any) -> None:
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)
        except RuntimeError as exc:  # Event loop closed.
            self.cancelled.set()
            raise ZipCancelled from exc

    def finish(self, error: BaseException | None = None) -> None:
        """Send any buffered bytes, then the end marker (or `error`). Never raises."""
        try:
            if error is None and self._buffer:
                self._send(bytes(self._buffer))
            self._buffer.clear()
            self._put(error if error is not None else self.END)
        except ZipCancelled:
            pass

    # -- reader side (event loop) ---------------------------------------------------------------------------

    async def get(self) -> Any:
        """Next chunk (bytes), ``ChunkPipe.END``, or the exception that stopped the writer."""
        item = await self._queue.get()
        if isinstance(item, bytes):
            self._slots.release()
        return item

    def cancel(self) -> None:
        self.cancelled.set()


class Gallery:
    """State and blocking operations behind the gallery routes.

    `roots` overrides where "output"/"input" live (tests); by default they come from ComfyUI's ``folder_paths``,
    falling back to ``<comfyui_base>/output`` and ``<comfyui_base>/input``.
    """

    def __init__(
        self,
        comfyui_base: Path | str,
        roots: Mapping[str, Path | str] | None = None,
        thumb_dir: Path | str | None = None,
    ) -> None:
        self.comfyui_base = Path(comfyui_base)
        self._roots = {name: Path(path) for name, path in (roots or {}).items()}
        self._thumb_dir = Path(thumb_dir) if thumb_dir is not None else None
        self._thumb_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._failed_thumbs: set[str] = set()
        self._zip_jobs: dict[str, ZipJob] = {}
        self._webp: bool | None = None
        self._scan_cache: collections.OrderedDict[tuple[str, str, bool], ScanResult] = collections.OrderedDict()
        self._scan_lock = threading.Lock()

    # -- paths ------------------------------------------------------------------------------------------------

    def root_directory(self, root: str) -> Path:
        if root not in ROOTS:
            raise GalleryError("Unknown root (expected 'output' or 'input')")
        if root in self._roots:
            return self._roots[root]
        return _folder_paths_directory(root) or self.comfyui_base / root

    def resolve(self, root: str, relative: Any, *, allow_empty: bool = False) -> tuple[Path, str]:
        """(absolute path, normalised relative POSIX path) for a client path. Raises GalleryError."""
        rel = safe_relative_path(relative, allow_empty=allow_empty)
        return self.root_directory(root).joinpath(*rel.parts), rel.as_posix() if rel.parts else ""

    @property
    def thumb_dir(self) -> Path:
        return self._thumb_dir if self._thumb_dir is not None else thumbnail_cache_directory()

    # -- listing ----------------------------------------------------------------------------------------------

    def list_files(
        self,
        root: str,
        subfolder: str = "",
        *,
        recursive: bool = False,
        query: str = "",
        sort: str = "newest",
        kind: str = "all",
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
        paths_only: bool = False,
    ) -> dict[str, Any]:
        """One page of the matching files, plus totals over all matches. Blocking; run in an executor.

        `query` is a case-insensitive substring of the path below `subfolder`. With `paths_only` every match is
        returned as parallel ``paths``/``sizes`` arrays (for "select all matching") instead of a page of items.
        """
        if sort not in SORTS:
            raise GalleryError(f"Invalid sort (expected one of {', '.join(SORTS)})")
        if kind not in KINDS:
            raise GalleryError(f"Invalid kind (expected one of {', '.join(KINDS)})")
        if offset < 0 or limit < 0:
            raise GalleryError("Invalid offset or limit")
        limit = min(limit, MAX_PAGE_SIZE)
        directory, sub = self.resolve(root, subfolder, allow_empty=True)
        prefix = f"{sub}/" if sub else ""

        try:
            scan = self._scan(directory, prefix, recursive)
            files, subfolders = list(scan.files), scan.subfolders  # Copy: the cached list must stay unsorted.
        except FileNotFoundError:
            if sub:
                raise GalleryError("Folder not found") from None
            files, subfolders = [], []  # The root itself does not exist yet: an empty gallery.
        except NotADirectoryError:
            raise GalleryError("Folder not found") from None
        except OSError as exc:
            raise GalleryError(f"Cannot read folder: {exc.strerror or exc}") from exc

        needle = query.strip().lower()
        if needle:
            files = [f for f in files if needle in f.path[len(prefix) :].lower()]
        if kind != "all":
            files = [f for f in files if f.kind == kind]
        sort_files(files, sort)

        data: dict[str, Any] = {
            "root": root,
            "subfolder": sub,
            "directory": str(directory),
            "subfolders": subfolders,
            "total": len(files),
            "total_size": sum(f.size for f in files),
            "offset": offset,
        }
        if paths_only:
            data["paths"] = [f.path for f in files]
            data["sizes"] = [f.size for f in files]
        else:
            data["items"] = [f.to_dict() for f in files[offset : offset + limit]]
        return data

    def _scan(self, directory: Path, prefix: str, recursive: bool) -> ScanResult:
        key = (os.fspath(directory), prefix, recursive)
        with self._scan_lock:
            cached = self._scan_cache.get(key)
        if cached is not None and cached.is_fresh():
            return cached
        result = scan_directory(directory, prefix, recursive)
        with self._scan_lock:
            self._scan_cache[key] = result
            self._scan_cache.move_to_end(key)
            while len(self._scan_cache) > LIST_CACHE_ENTRIES:
                self._scan_cache.popitem(last=False)
        return result

    def invalidate_listings(self) -> None:
        with self._scan_lock:
            self._scan_cache.clear()

    # -- files ------------------------------------------------------------------------------------------------

    def existing_file(self, root: str, relative: Any) -> tuple[Path, os.stat_result]:
        """Absolute path and stat of an existing regular file. Raises GalleryError (400/404)."""
        path, _ = self.resolve(root, relative)
        try:
            st = path.stat()
        except OSError:
            raise GalleryError("File not found") from None
        if not stat.S_ISREG(st.st_mode):
            raise GalleryError("File not found")
        return path, st

    def delete_files(self, root: str, paths: Any) -> dict[str, Any]:
        """Delete files (never folders). Blocking. Returns ``{deleted: [...], failed: [{path, error}]}``."""
        directory = self.root_directory(root)
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise GalleryError("paths must be a list of strings")
        if not paths:
            raise GalleryError("No paths given")
        if len(paths) > MAX_DELETE_BATCH:
            raise GalleryError(f"Too many files in one request ({len(paths)}; max {MAX_DELETE_BATCH})")

        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        for original in dict.fromkeys(paths):
            try:
                rel = safe_relative_path(original)
            except GalleryError as exc:
                failed.append({"path": original, "error": str(exc)})
                continue
            target = directory.joinpath(*rel.parts)
            try:
                st = target.stat()
                if stat.S_ISDIR(st.st_mode):
                    failed.append({"path": original, "error": "Not a file"})
                    continue
                target.unlink()
            except FileNotFoundError:
                failed.append({"path": original, "error": "File not found"})
                continue
            except OSError as exc:
                failed.append({"path": original, "error": exc.strerror or str(exc)})
                continue
            deleted.append(original)
            self._drop_thumbnails(root, rel.as_posix(), st)
        if deleted:
            self.invalidate_listings()
        return {"deleted": deleted, "failed": failed}

    # -- thumbnails -------------------------------------------------------------------------------------------

    @staticmethod
    def thumb_size(requested: int) -> int:
        return next((s for s in THUMB_SIZES if s >= requested), THUMB_SIZES[-1])

    def _thumb_key(self, root: str, rel: str, st: os.stat_result, size: int) -> str:
        raw = f"{THUMB_CACHE_VERSION}\0{root}\0{rel}\0{st.st_mtime_ns}\0{st.st_size}\0{size}"
        return hashlib.sha1(raw.encode("utf-8", "surrogatepass")).hexdigest()

    def _thumb_path(self, key: str) -> Path:
        return self.thumb_dir / key[:2] / f"{key}.{'webp' if self.webp_supported() else 'jpg'}"

    def webp_supported(self) -> bool:
        if self._webp is None:
            try:
                from PIL import features

                self._webp = bool(features.check("webp"))
            except Exception:
                self._webp = False
        return self._webp

    @property
    def thumb_content_type(self) -> str:
        return "image/webp" if self.webp_supported() else "image/jpeg"

    def thumbnail_lookup(self, root: str, relative: Any, size: int) -> tuple[str, Path, Path, bytes | None]:
        """(etag key, source, cache path, cached bytes or None). Blocking. Raises GalleryError/ThumbnailError."""
        path, st = self.existing_file(root, relative)
        rel = safe_relative_path(relative).as_posix()
        if file_kind(path.name) != "image":
            raise ThumbnailError("No thumbnail for this file type")
        key = self._thumb_key(root, rel, st, size)
        if key in self._failed_thumbs:
            raise ThumbnailError("Cannot create a thumbnail for this file")
        cache = self._thumb_path(key)
        try:
            return key, path, cache, cache.read_bytes()
        except OSError:
            return key, path, cache, None

    def render_thumbnail(self, key: str, source: Path, cache: Path, size: int) -> bytes:
        """Render, cache and return a thumbnail. Blocking (CPU); run on ``thumb_executor``."""
        try:
            data = render_thumbnail_bytes(source, size, webp=self.webp_supported())
        except ThumbnailError:
            if len(self._failed_thumbs) >= MAX_FAILED_THUMBS:
                self._failed_thumbs.clear()
            self._failed_thumbs.add(key)
            raise
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            temp = cache.with_name(f"{cache.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            temp.write_bytes(data)
            os.replace(temp, cache)
        except OSError as exc:  # Cache dir not writable: still serve the thumbnail.
            logger.warning("Could not cache thumbnail %s: %s", cache, exc)
        return data

    @property
    def thumb_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._thumb_executor is None:
            self._thumb_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=THUMB_WORKERS, thread_name_prefix="spooktools-thumb"
            )
        return self._thumb_executor

    def _drop_thumbnails(self, root: str, rel: str, st: os.stat_result) -> None:
        for size in THUMB_SIZES:
            try:
                self._thumb_path(self._thumb_key(root, rel, st, size)).unlink()
            except OSError:
                pass

    # -- zip --------------------------------------------------------------------------------------------------

    def prepare_zip(self, root: str, paths: Any) -> tuple[ZipJob, list[str]]:
        """Validate a selection for download. Blocking. Returns the job and the paths that no longer exist."""
        directory = self.root_directory(root)
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise GalleryError("paths must be a list of strings")
        if not paths:
            raise GalleryError("No paths given")
        if len(paths) > MAX_ZIP_FILES:
            raise GalleryError(f"Too many files in one archive ({len(paths)}; max {MAX_ZIP_FILES})")

        kept: list[str] = []
        missing: list[str] = []
        total = 0
        for rel in dict.fromkeys(safe_relative_path(p).as_posix() for p in paths):
            try:
                st = directory.joinpath(*rel.split("/")).stat()
            except OSError:
                missing.append(rel)
                continue
            if not stat.S_ISREG(st.st_mode):
                missing.append(rel)
                continue
            kept.append(rel)
            total += st.st_size
        if not kept:
            raise GalleryError("Selected files not found")
        filename = f"spooktools-{root}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        job = ZipJob(root, directory, kept, total, filename, expires=time.monotonic() + ZIP_TOKEN_TTL)
        return job, missing

    def store_zip_job(self, job: ZipJob) -> str:
        self.expire_zip_jobs()
        token = secrets.token_urlsafe(24)
        self._zip_jobs[token] = job
        return token

    def take_zip_job(self, token: str) -> ZipJob | None:
        """The job for `token` (counting one use), or None if unknown, expired or used up."""
        self.expire_zip_jobs()
        job = self._zip_jobs.get(token)
        if job is None:
            return None
        job.uses_left -= 1
        if job.uses_left <= 0:
            del self._zip_jobs[token]
        return job

    def expire_zip_jobs(self) -> None:
        now = time.monotonic()
        for token, job in list(self._zip_jobs.items()):
            if job.expires <= now:
                del self._zip_jobs[token]

    @staticmethod
    def write_zip(job: ZipJob, pipe: ChunkPipe) -> None:
        """Write the archive into `pipe` (run in a dedicated thread). Never raises.

        Entries are stored uncompressed (images and videos are already compressed) under their root-relative
        paths. zipfile writes data descriptors because the pipe is not seekable, and switches to zip64 where an
        entry, an offset or the entry count needs it. Files that vanished since the selection are skipped.
        """
        try:
            with zipfile.ZipFile(pipe, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for rel in job.paths:
                    if pipe.cancelled.is_set():
                        raise ZipCancelled
                    try:
                        source = open(job.directory.joinpath(*rel.split("/")), "rb")
                    except OSError as exc:
                        logger.info("Zip download: skipping %s (%s)", rel, exc.strerror or exc)
                        continue
                    with source:
                        st = os.fstat(source.fileno())
                        if not stat.S_ISREG(st.st_mode):
                            continue
                        info = zipfile.ZipInfo(rel, date_time=_zip_date_time(st.st_mtime))
                        info.compress_type = zipfile.ZIP_STORED
                        info.file_size = st.st_size  # zipfile picks zip64 for this entry from the expected size.
                        info.external_attr = (stat.S_IFREG | 0o644) << 16
                        with archive.open(info, "w") as target:
                            shutil.copyfileobj(source, target, ZIP_READ_CHUNK)
        except ZipCancelled:
            logger.debug("Zip download of %d files cancelled", len(job.paths))
            return
        except Exception as exc:
            if pipe.cancelled.is_set():
                return
            logger.exception("Zip download failed")
            pipe.finish(exc)
            return
        pipe.finish()
