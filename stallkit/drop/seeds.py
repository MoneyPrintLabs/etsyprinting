"""Working out what a design is *about*, from its filename.

This is the honest weak point of the whole pipeline and it is better to say so than
to hide it. `mountain-sunset.png` yields a usable concept. `IMG_2043.png` does not,
and no amount of cleverness changes that — the information is simply not there.

So this module's real job is not extraction, it is **knowing when it failed**. A junk
seed is flagged, surfaced to the seller, and never quietly turned into a confident
title that would put a wrong listing in their shop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Camera and screenshot names. These carry a number, never a concept.
_CAMERA = re.compile(
    r"^(img|dsc|dscn|dscf|pxl|gopr|mvimg|photo|image|screenshot|screen[\s_-]?shot|"
    r"ekran[\s_-]?g[oö]r[uü]nt[uü]s[uü]|adsiz|untitled|unnamed|document|scan)"
    # Anything after is a timestamp, a counter or a date — never a product.
    r"[\s_.\-]*[\d\s_.\-]*$",
    re.IGNORECASE,
)

# Words that describe the file's revision, not the product.
# Deliberately excludes "photo", "image" and "frame": those appear in real product
# names ("photo frame", "image transfer"), and stripping them would lose the concept.
# Tokens that are never part of a product name, wherever they appear: camera and
# screenshot prefixes, and words that only ever describe the file rather than the thing.
_ALWAYS_NOISE = {
    "finalv", "copy", "kopya", "duzenlenmis", "untitled", "adsiz", "temp", "tmp",
    "printfile", "unnamed",
    # Camera and screenshot prefixes, for when they survive as a bare token.
    "img", "dsc", "dscn", "dscf", "pxl", "gopr", "mvimg", "screenshot", "scan",
}

# Revision markers. These are ordinary product words in the middle of a name — "new" is
# in "New York", "son" is in Turkish names, "print" and "design" describe real products —
# so they only count as noise where they actually behave like a marker: at the end, after
# the name proper. Stripping them everywhere would turn "new-york-skyline" into "york
# skyline" and mislabel two of the biggest print categories on Etsy.
_TRAILING_NOISE = {
    "final", "new", "yeni", "edit", "edited", "draft", "taslak", "test", "deneme",
    "son", "orig", "original", "export", "output", "asset", "file", "version",
    "revised", "fix", "print", "design", "tasarim", "artwork",
}

# A version is `v3` or `1.2`, never a bare `66` — that is a route number, a year or a
# model, and dropping it anywhere in the name would cost products their identity. Plain
# counters are handled as trailing tokens instead, where they actually behave like one.
_VERSION = re.compile(r"^(v\d+(\.\d+)*|\d+(\.\d+)+)$", re.IGNORECASE)
_HEXISH = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)
_LEADING_INDEX = re.compile(r"^\d{1,4}[\s._-]+")
_SEPARATORS = re.compile(r"[\s._\-+]+")


@dataclass
class Seed:
    """A product concept derived from a filename."""

    text: str
    source: str
    is_junk: bool = False
    reason: str = ""

    @property
    def words(self) -> list[str]:
        return self.text.split()

    def __bool__(self) -> bool:
        return bool(self.text) and not self.is_junk


def derive(path: Path, *, folder_fallback: bool = True) -> Seed:
    """Turn a design's path into a concept seed.

    Falls back to the containing folder name when the filename alone is junk, which
    is what saves a camera-roll export sitting in a folder called `mountain sunset`.
    """
    stem = path.stem
    seed = _from_text(stem, source=stem)
    if seed or not folder_fallback:
        return seed

    parent = path.parent.name
    if parent:
        fallback = _from_text(parent, source=stem)
        if fallback:
            return Seed(
                text=fallback.text,
                source=stem,
                is_junk=False,
                reason=f"filename carried no concept; used the folder name {parent!r}",
            )
    return seed


def _from_text(raw: str, *, source: str) -> Seed:
    text = raw.strip()
    if not text:
        return Seed("", source, True, "empty filename")

    if _CAMERA.match(text):
        return Seed("", source, True, f"{raw!r} looks like a camera or screenshot name")

    # `001-retro-sunset-surf` -> `retro-sunset-surf`
    text = _LEADING_INDEX.sub("", text)
    tokens = [t for t in _SEPARATORS.split(text) if t]

    kept = []
    for token in tokens:
        lowered = token.lower()
        if lowered in _ALWAYS_NOISE or _VERSION.match(token) or _HEXISH.match(token):
            continue
        kept.append(lowered)

    # A bare counter is the very last thing in a name, so that is the only place it is
    # dropped: `mountain-sunset-2` loses its 2, `route-66-poster` keeps its 66. Checked
    # before the markers and never again after, or `boeing-747-print` would lose the 747
    # as soon as `print` came off and exposed it.
    while kept and kept[-1].isdigit():
        kept.pop()

    # Markers trail the name rather than interrupting it, and they stack:
    # `mountain-sunset-final-edit`.
    while kept and kept[-1] in _TRAILING_NOISE:
        kept.pop()

    if not kept:
        return Seed("", source, True, f"{raw!r} carries no describable words")

    concept = " ".join(kept)
    letters = sum(1 for ch in concept if ch.isalpha())
    if letters < 3:
        return Seed("", source, True, f"{raw!r} is too short to describe a product")

    return Seed(concept, source)


def group(seeds: list[Seed]) -> dict[str, list[Seed]]:
    """Group by concept so research runs once per distinct idea, not once per file.

    A hundred products across eighteen concepts is eighteen searches, not a hundred —
    which is the difference between comfortable and rate-limited on a 5 QPS app.
    """
    grouped: dict[str, list[Seed]] = {}
    for seed in seeds:
        if seed:
            grouped.setdefault(seed.text, []).append(seed)
    return grouped
