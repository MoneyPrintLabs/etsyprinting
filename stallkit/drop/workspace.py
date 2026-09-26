"""The desktop folder that is the whole user interface for input.

Explorer is already the best drag-and-drop target that will ever exist on this
machine — multi-select, thumbnails, rename in place, works over OneDrive and Remote
Desktop — and it costs nothing to use. So the input surface is a folder, and the
folder names itself in the order you use it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ValidationError

MOCKUPS_DIR = "1-MOCKUPS"
PRODUCTS_DIR = "2-PRODUCTS"
DRAFTS_DIR = "3-DRAFTS"
ARCHIVE_DIR = "archive"

# Calibration previews go under 3-DRAFTS, never beside the templates: anything with an
# image extension in 1-MOCKUPS *is* a mockup as far as mockup_files() is concerned, so a
# preview written there would come back as a template to composite designs onto.
CALIBRATION_DIR = "calibration"

TEMPLATE_FILE = "product.json"
POSITIONS_FILE = "positions.json"

# Stems ending in one of these are a generator's own preview of the artwork beside it,
# not a product. English and Turkish spellings are both covered.
PREVIEW_SUFFIXES = ("-vitrin", "-preview", "-onizleme", "-thumb", "-mockup")

# Design files Pillow can open without an extra. HEIC is deliberately absent: it
# needs pillow-heif, and a missing-extra message beats a decode traceback.
#
# This is the INPUT set and it stays wide on purpose — it is not what Etsy accepts.
# That is `client.UPLOADABLE_SUFFIXES`, which is only JPG/PNG/GIF. A .tif mockup or
# a .bmp design is perfectly usable here because the compositor writes JPEG either
# way, and narrowing this list would make a file the seller dropped into the folder
# simply not appear — the one failure a drag-and-drop input surface must never have.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

README_TEXT = """\
ETSY STUDIO
===========

1-MOCKUPS   Put your mockup templates here (photos of a blank shirt, mug, poster).
            You only do this once. If a design lands in the wrong place on one,
            run: stallkit drop calibrate --preview

2-PRODUCTS  Put the designs you want listed here. This is the folder you use every
            time. One loose design = one listing. For ready photos, create one
            folder per product and put its numbered images inside (01, 02, ...).
            Name the folder after the product. Run: stallkit drop auto

3-DRAFTS    What comes out: composited images and review.csv.
            `stallkit drop run` stops here so you can check review.csv first.
            `stallkit drop auto` uploads the drafts straight away, in one step.

archive/    Optional manual archive. Files are not moved automatically.

upload-history.json (next to these folders) records every automatic upload.
Keep it: it is what stops `drop auto` from uploading a product twice.

Nothing here is ever published. Listings are created as DRAFTS in your Etsy shop
and stay invisible to buyers until you publish them yourself.

--------------------------------------------------------------------------------

ETSY STUDIO (TR)
================

1-MOCKUPS   Mockup sablonlarini buraya koy (bos tisort, kupa, poster fotograflari).
            Bunu sadece bir kez yaparsin. Tasarim yanlis yere denk geliyorsa
            komut: stallkit drop calibrate --preview

2-PRODUCTS  Listelemek istedigin tasarimlari buraya koy. Her seferinde
            kullanacagin klasor bu. Hazir mockuplar icin her urune ayri bir klasor
            ac; 01, 02 diye siraladigin resimleri icine koy. Klasore urunun adini
            ver. Komut: stallkit drop auto. Bir urun klasoru = bir listing.

3-DRAFTS    Cikan sonuc: giydirilmis gorseller ve review.csv.
            `stallkit drop run` burada durur; once review.csv'ye bakarsin.
            `stallkit drop auto` ise taslaklari tek adimda hemen yukler.

archive/    Istersen elle arsivleyebilirsin; otomatik tasima yapilmaz.

upload-history.json (bu klasorlerin yaninda) her otomatik yuklemeyi kaydeder.
Silme: `drop auto`nun ayni urunu iki kez yuklemesini engelleyen bu dosya.

Hicbir sey yayinlanmaz. Listingler Etsy magazanda TASLAK olarak olusturulur ve sen
kendin yayinlayana kadar alicilar goremez.
"""


@dataclass
class Workspace:
    root: Path

    @property
    def mockups(self) -> Path:
        return self.root / MOCKUPS_DIR

    @property
    def products(self) -> Path:
        return self.root / PRODUCTS_DIR

    @property
    def drafts(self) -> Path:
        return self.root / DRAFTS_DIR

    @property
    def archive(self) -> Path:
        return self.root / ARCHIVE_DIR

    @property
    def calibration(self) -> Path:
        return self.drafts / CALIBRATION_DIR

    @property
    def template_path(self) -> Path:
        return self.root / TEMPLATE_FILE

    @property
    def positions_path(self) -> Path:
        return self.mockups / POSITIONS_FILE

    def create(self) -> Workspace:
        for path in (self.mockups, self.products, self.drafts, self.archive):
            path.mkdir(parents=True, exist_ok=True)
        readme = self.root / "README.txt"
        # The file is generated, so it is refreshed whenever the text changes; an
        # older copy would keep describing behaviour the tool no longer has.
        try:
            current = readme.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current = None
        if current != README_TEXT:
            readme.write_text(README_TEXT, encoding="utf-8")
        return self

    def require(self) -> Workspace:
        if not self.root.is_dir():
            raise ValidationError(
                f"No workspace at {self.root}. Create one with: stallkit drop init"
            )
        missing = [d.name for d in (self.mockups, self.products) if not d.is_dir()]
        if missing:
            raise ValidationError(
                f"{self.root} is missing {', '.join(missing)}. "
                "Re-run `stallkit drop init` to repair it."
            )
        return self

    # --- contents ---------------------------------------------------------------

    def mockup_files(self) -> list[Path]:
        return _images(self.mockups)

    def product_files(self, *, exclude_suffixes: tuple[str, ...] = PREVIEW_SUFFIXES) -> list[Path]:
        """Designs waiting to be listed, minus the preview renders beside them.

        Print-on-demand generators habitually write a small showcase render next to
        the full-resolution artwork, named from the same stem. Both land in the same
        folder, and listing the preview would publish a downscaled stand-in as though
        it were the product — so the known suffixes are skipped by default, and the
        argument is there for a generator that uses a different one.
        """
        files = _images(self.products)
        return [f for f in files if not any(f.stem.endswith(s) for s in exclude_suffixes)]

    def product_groups(self) -> list[tuple[Path, list[Path]]]:
        """Loose designs retain their old behavior; each folder is one ready product.

        Folder images are already finished mockups, including transparent PNGs.
        Number them 01, 02, ... to choose their listing order.
        """
        groups = [(path, []) for path in self.product_files()]
        for folder in sorted(self.products.iterdir(), key=lambda p: p.name.casefold()):
            if folder.is_dir() and not folder.is_symlink():
                images = _images(folder)
                if images:
                    groups.append((folder, sorted(images, key=_natural_key)))
        return groups

    def read_template(self) -> dict[str, Any]:
        if not self.template_path.exists():
            raise ValidationError(
                "No product template yet. Point at a listing you built by hand:\n"
                "  stallkit drop template --from-listing <listing_id>\n"
                "If your shop is empty, create one listing properly in Etsy first — "
                "every draft copies its settings."
            )
        try:
            return json.loads(self.template_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{self.template_path} is not valid JSON: {exc}") from exc

    def write_template(self, data: dict[str, Any]) -> None:
        self.template_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda p: p.name.lower(),
    )


def _natural_key(path: Path) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.name.casefold())]


def desktop_dir() -> Path:
    """Desktop if there is one, home otherwise. Never guesses a localised name."""
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


def default_root() -> Path:
    """The selected shop's products folder.

    "Etsy Studio" for the first shop and "Etsy Studio - <shop id>" for each further
    one, so two shops never share products, a template or an upload history. The id
    rather than the Etsy shop name, so the folder does not give the shop away in a
    screenshot.
    """
    from ..config import base_home, home_dir

    home = home_dir()
    name = "Etsy Studio" if home == base_home() else f"Etsy Studio - {home.name}"
    return desktop_dir() / name
