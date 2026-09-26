"""Serving files safely: static assets, workspace images and their thumbnails.

`resolve_inside(root, rel)` is the one gate: it returns a path only when the
resolved target lies inside `root`, so "..", absolute paths, drive letters and
symlinks pointing out of the folder all end in "not found".
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path

# Hard-coded on purpose: `mimetypes` reads the Windows registry, where .js is often
# text/plain — and a module script served as text/plain with nosniff never runs.
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    # Workspace images the drop folder accepts; browsers show some of them.
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
DEFAULT_TYPE = "application/octet-stream"

THUMB_MIN, THUMB_MAX = 64, 1600
_thumb_lock = threading.Lock()


def content_type_for(path: Path | str) -> str:
    return MIME.get(Path(path).suffix.lower(), DEFAULT_TYPE)


def resolve_inside(root: Path, rel: str, *, follow_links: bool = True) -> Path | None:
    """`root / rel` if that is a path inside `root`, else None. Never raises.

    "..", empty and drive-like segments are refused outright. With follow_links
    (the default, for the user's folders) symlinks are resolved and must still end
    inside `root`. follow_links=False is for the app's own static files: a frozen
    macOS app may ship them as symlinks into Contents/Resources, which are ours.
    """
    if not rel or "\x00" in rel:
        return None
    rel = rel.replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    try:
        base = root.resolve()
        target = base.joinpath(*parts)
        if follow_links:
            target = target.resolve()
        target.relative_to(base)
    except (OSError, ValueError, RuntimeError):
        return None
    return target


def workspace_image(root: Path, rel: str) -> Path | None:
    """An existing image file inside the workspace root, or None."""
    from ..drop.workspace import IMAGE_SUFFIXES

    target = resolve_inside(root, rel)
    if target is None or target.suffix.lower() not in IMAGE_SUFFIXES or not target.is_file():
        return None
    return target


def thumbnail(source: Path, width: int, cache: Path) -> Path:
    """A JPEG of `source` at most `width` px wide, upright, sRGB, flattened on white.

    Cached under `cache`, keyed by path + mtime + size + width, so an edited file
    gets a new thumbnail and an unchanged one is rendered once.
    """
    from PIL import Image

    from ..drop import mockup

    width = max(THUMB_MIN, min(THUMB_MAX, int(width)))
    stat = source.stat()
    key = hashlib.sha256(
        f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{width}".encode()
    ).hexdigest()[:32]
    target = cache / f"{key}.jpg"
    if target.is_file():
        return target
    with Image.open(source) as opened:
        if opened.format == "JPEG":
            # Let libjpeg decode at a fraction of the size when that is plenty.
            opened.draft("RGB", (width, width * 4))
        image = mockup._as_displayed(opened)
        image = mockup.flatten_onto(image, mockup.WHITE)
        if image.width > width:
            height = max(1, round(image.height * width / image.width))
            image = image.resize((width, height), Image.LANCZOS)
        cache.mkdir(parents=True, exist_ok=True)
        tmp = cache / f"{key}.{threading.get_ident()}.tmp"
        image.save(tmp, format="JPEG", quality=85, optimize=True)
    with _thumb_lock:
        tmp.replace(target)
    return target


def open_path(path: Path) -> None:
    """Show a folder or file in the system's own file manager."""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 — a local folder of the user's own
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
