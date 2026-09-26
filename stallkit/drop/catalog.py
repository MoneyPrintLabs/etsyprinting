"""What each mockup is: its product type, its colour, and whether drafts use it.

The mockup files themselves stay the source of truth — whatever image sits in
1-MOCKUPS is a mockup. This module only keeps the facts a file name cannot hold
reliably, in `1-MOCKUPS/mockups.json`:

    {"shirt-white.jpg": {"type": "tshirt", "color": "Beyaz", "enabled": true}}

A mockup with no entry gets a guess from its file name and is enabled. An entry
whose file is gone is ignored (kept on disk, so renaming the file back restores it).

Print areas stay in positions.json (see `mockup.load_positions`); the helpers at
the bottom answer "which rectangle does this mockup use, and why" exactly the way
`pipeline.run` decides it, so a screen can never show one area and composite another.
"""

from __future__ import annotations

import io
import json
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from ..errors import ValidationError
from . import mockup
from .workspace import IMAGE_SUFFIXES, Workspace

CATALOG_FILE = "mockups.json"

TYPES = (
    "tshirt",
    "sweatshirt",
    "hoodie",
    "mug",
    "poster",
    "canvas",
    "phone_case",
    "tote",
    "pillow",
    "sticker",
    "other",
)

# Etsy takes 20 images per listing, and every draft also carries the flat design.
MAX_ENABLED = 19

SOURCE_OWN = "own"
SOURCE_SAME_SIZE = "same_size"
SOURCE_DEFAULT = "default"

# Words are compared after folding: lower case, Turkish letters to ASCII (ı→i, ş→s,
# ç→c, ğ→g, ö→o, ü→u), accents dropped. Checked in this order, so "hoodie" wins over
# "shirt" and "canvas" over "print".
_TYPE_WORDS: tuple[tuple[str, frozenset[str]], ...] = (
    ("hoodie", frozenset({"hoodie", "hoodies", "hoody", "kapusonlu", "kapsonlu"})),
    ("sweatshirt", frozenset({"sweatshirt", "sweatshirts", "sweat", "crewneck"})),
    ("tshirt", frozenset({"tshirt", "tshirts", "tee", "tees", "shirt", "tisort", "tisortu"})),
    ("mug", frozenset({"mug", "mugs", "cup", "cups", "kupa", "bardak"})),
    ("phone_case", frozenset({"phone", "phonecase", "case", "iphone", "samsung", "kilif"})),
    ("tote", frozenset({"tote", "totebag", "bag", "canta"})),
    ("pillow", frozenset({"pillow", "cushion", "yastik", "kirlent"})),
    ("sticker", frozenset({"sticker", "stickers", "decal", "etiket"})),
    ("canvas", frozenset({"canvas", "tuval"})),
    ("poster", frozenset({"poster", "print", "frame", "framed", "cerceve", "afis", "wallart"})),
)

# folded word -> display word (Turkish, the app's first language).
_COLOR_WORDS: tuple[tuple[str, frozenset[str]], ...] = (
    ("Beyaz", frozenset({"white", "beyaz"})),
    ("Siyah", frozenset({"black", "siyah"})),
    ("Lacivert", frozenset({"navy", "lacivert"})),
    ("Krem", frozenset({"cream", "krem", "natural", "naturel", "ivory", "ecru"})),
    ("Gri", frozenset({"grey", "gray", "gri", "heather", "ash"})),
    ("Kırmızı", frozenset({"red", "kirmizi"})),
    ("Bordo", frozenset({"maroon", "burgundy", "bordo"})),
    ("Mavi", frozenset({"blue", "mavi"})),
    ("Yeşil", frozenset({"green", "yesil"})),
    ("Haki", frozenset({"olive", "khaki", "haki"})),
    ("Pembe", frozenset({"pink", "pembe"})),
    ("Sarı", frozenset({"yellow", "sari"})),
    ("Turuncu", frozenset({"orange", "turuncu"})),
    ("Mor", frozenset({"purple", "mor", "lila", "lilac"})),
    ("Kahverengi", frozenset({"brown", "kahverengi", "kahve"})),
    ("Bej", frozenset({"beige", "bej", "sand", "kum"})),
    ("Meşe", frozenset({"oak", "mese", "wood", "wooden", "ahsap"})),
    ("Ceviz", frozenset({"walnut", "ceviz"})),
)

_FOLD = str.maketrans({"ı": "i", "ş": "s", "ç": "c", "ğ": "g", "ö": "o", "ü": "u"})

# Read-modify-write of mockups.json / positions.json from several request threads.
_LOCK = threading.RLock()


@dataclass
class MockupInfo:
    name: str
    type: str
    color: str
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "color": self.color, "enabled": self.enabled}


def _fold(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "i").lower().translate(_FOLD)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _words(filename: str) -> list[str]:
    stem = Path(filename).stem
    # camelCase and digits split too: "tshirtWhite01" -> tshirt, white.
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
    folded = _fold(stem)
    words = [w for w in re.split(r"[^a-z]+", folded) if w]
    # "t-shirt" / "t shirt" arrive as two words.
    joined = [a + b for a, b in zip(words, words[1:]) if a == "t" and b.startswith("shirt")]
    return words + joined


def guess(filename: str) -> tuple[str, str]:
    """(type, colour) read from a file name; ("other", "") when nothing matches.

    English and Turkish words are both understood: `kupa-beyaz.jpg` and
    `mug_white.png` both give ("mug", "Beyaz"). The colour is a display word in
    Turkish, the app's first language.
    """
    words = set(_words(filename))
    kind = next((name for name, keys in _TYPE_WORDS if words & keys), "other")
    color = next((label for label, keys in _COLOR_WORDS if words & keys), "")
    return kind, color


def catalog_path(ws: Workspace) -> Path:
    return ws.mockups / CATALOG_FILE


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _info(name: str, raw: Any) -> MockupInfo:
    kind, color = guess(name)
    if not isinstance(raw, dict):
        return MockupInfo(name, kind, color, True)
    stored_type = raw.get("type")
    stored_color = raw.get("color")
    enabled = raw.get("enabled")
    return MockupInfo(
        name=name,
        type=stored_type if stored_type in TYPES else kind,
        color=str(stored_color).strip() if isinstance(stored_color, str) else color,
        enabled=enabled if isinstance(enabled, bool) else True,
    )


def load(ws: Workspace) -> dict[str, MockupInfo]:
    """Every mockup file, in `mockup_files()` order, with its catalog facts."""
    with _LOCK:
        raw = _read_json(catalog_path(ws))
        return {path.name: _info(path.name, raw.get(path.name)) for path in ws.mockup_files()}


def _mockup_path(ws: Workspace, name: str) -> Path:
    """The file for `name`, which must be a mockup in 1-MOCKUPS — never anything else."""
    if not name or name != Path(name).name or name in (".", ".."):
        raise FileNotFoundError(name)
    for path in ws.mockup_files():
        if path.name == name:
            return path
    raise FileNotFoundError(name)


def update(
    ws: Workspace,
    name: str,
    *,
    type: str | None = None,  # noqa: A002 — the catalog's own field name
    color: str | None = None,
    enabled: bool | None = None,
) -> MockupInfo:
    """Change what is recorded about one mockup. FileNotFoundError if there is none."""
    _mockup_path(ws, name)
    if type is not None and type not in TYPES:
        raise ValidationError(f"Unknown mockup type {type!r}. Use one of: {', '.join(TYPES)}")
    if color is not None:
        color = str(color).strip()
        if len(color) > 40:
            raise ValidationError("A colour name can be at most 40 characters.")
    with _LOCK:
        path = catalog_path(ws)
        raw = _read_json(path)
        info = _info(name, raw.get(name))
        if type is not None:
            info.type = type
        if color is not None:
            info.color = color
        if enabled is not None:
            info.enabled = bool(enabled)
        raw[name] = {"type": info.type, "color": info.color, "enabled": info.enabled}
        _write_json(path, raw)
        return info


def remove(ws: Workspace, name: str) -> None:
    """Delete a mockup file and everything recorded about it (catalog and print area)."""
    target = _mockup_path(ws, name)
    with _LOCK:
        target.unlink()
        path = catalog_path(ws)
        raw = _read_json(path)
        if raw.pop(name, None) is not None:
            _write_json(path, raw)
        positions = _read_json(ws.positions_path)
        if positions.pop(name, None) is not None:
            _write_json(ws.positions_path, dict(sorted(positions.items())))


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}


def safe_name(filename: str) -> str:
    """A file name that is safe on Windows and macOS, keeping what can be kept."""
    base = re.split(r"[\\/]", str(filename or ""))[-1]
    base = _UNSAFE.sub("", base).strip().strip(".").strip()
    stem, suffix = Path(base).stem.strip(), Path(base).suffix.lower()
    stem = stem[:100].rstrip(". ") or "mockup"
    if stem.lower() in _RESERVED:
        stem = f"mockup-{stem}"
    return f"{stem}{suffix}"


def add(ws: Workspace, filename: str, data: bytes) -> str:
    """Save an uploaded mockup into 1-MOCKUPS and return the name it was saved under.

    Refuses anything that is not an image Pillow can read. Never overwrites: a
    second `shirt.jpg` becomes `shirt-2.jpg`.
    """
    name = safe_name(filename)
    suffix = Path(name).suffix
    if suffix not in IMAGE_SUFFIXES:
        raise ValidationError(
            f"{name}: mockups can be {', '.join(sorted(s.lstrip('.') for s in IMAGE_SUFFIXES))} images."
        )
    if not data:
        raise ValidationError(f"{name} is empty.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValidationError(f"{name} is not an image that can be read ({exc}).") from exc
    ws.mockups.mkdir(parents=True, exist_ok=True)
    stem = Path(name).stem
    with _LOCK:
        number = 1
        while True:
            candidate = name if number == 1 else f"{stem}-{number}{suffix}"
            target = ws.mockups / candidate
            try:
                with target.open("xb") as handle:
                    handle.write(data)
            except FileExistsError:
                number += 1
                continue
            return candidate


def enabled_mockups(ws: Workspace) -> list[Path]:
    """The mockups drafts are made with: enabled ones, in folder order, at most 19."""
    infos = load(ws)
    return [p for p in ws.mockup_files() if infos.get(p.name, None) is None or infos[p.name].enabled][
        :MAX_ENABLED
    ]


# --- print areas ------------------------------------------------------------------


def effective_areas(
    ws: Workspace, *, sizes: dict[str, tuple[int, int]] | None = None
) -> dict[str, tuple[mockup.PrintArea, str]]:
    """(area, source) for every mockup, decided exactly as `pipeline.run` decides it.

    source is "own" (its positions.json entry), "same_size" (borrowed from the first
    calibrated mockup, by name, with identical pixel dimensions) or "default".
    """
    available = ws.mockup_files()
    positions = mockup.load_positions(ws.positions_path)
    if sizes is None:
        sizes = mockup.mockup_sizes(available)
    by_size: dict[tuple[int, int], mockup.PrintArea] = {}
    for name in sorted(positions):
        if name in sizes:
            by_size.setdefault(sizes[name], positions[name])
    out: dict[str, tuple[mockup.PrintArea, str]] = {}
    for path in available:
        own = positions.get(path.name)
        if own is not None:
            out[path.name] = (own, SOURCE_OWN)
            continue
        borrowed = by_size.get(sizes.get(path.name, (0, 0)))
        if borrowed is not None:
            out[path.name] = (borrowed, SOURCE_SAME_SIZE)
        else:
            out[path.name] = (mockup.DEFAULT_PRINT_AREA, SOURCE_DEFAULT)
    return out


def effective_area(ws: Workspace, name: str) -> tuple[mockup.PrintArea, str]:
    """(area, source) for one mockup. FileNotFoundError if it is not a mockup."""
    _mockup_path(ws, name)
    return effective_areas(ws)[name]


def same_size_names(
    ws: Workspace, name: str, *, sizes: dict[str, tuple[int, int]] | None = None
) -> list[str]:
    """Every mockup (in folder order, `name` included) with `name`'s pixel dimensions.

    Empty when `name` is not a mockup or its size cannot be read.
    """
    mockups = ws.mockup_files()
    if sizes is None:
        sizes = mockup.mockup_sizes(mockups)
    size = sizes.get(name)
    if not size or name not in {p.name for p in mockups}:
        return []
    return [p.name for p in mockups if sizes.get(p.name) == size]


def save_area(
    ws: Workspace,
    name: str,
    area: mockup.PrintArea,
    *,
    same_size: bool = False,
    dry_run: bool = False,
    sizes: dict[str, tuple[int, int]] | None = None,
) -> list[str]:
    """Give `name` (and, with same_size, every mockup of its size) the print area.

    Returns the names that were — or with dry_run, would be — changed.
    """
    _mockup_path(ws, name)
    targets = [name]
    if same_size:
        targets = same_size_names(ws, name, sizes=sizes)
        if not targets:
            raise ValidationError(
                f"Cannot read the pixel size of {name}, so there is nothing to match."
            )
    if dry_run:
        return targets
    with _LOCK:
        positions = mockup.load_positions(ws.positions_path)
        for target in targets:
            positions[target] = area
        mockup.save_positions(ws.positions_path, positions)
    return targets


def clear_area(ws: Workspace, name: str) -> bool:
    """Forget `name`'s own print area. False if it had none (already on the default)."""
    _mockup_path(ws, name)
    with _LOCK:
        positions = mockup.load_positions(ws.positions_path)
        if positions.pop(name, None) is None:
            return False
        mockup.save_positions(ws.positions_path, positions)
    return True
