"""Folder of designs in, validated listing CSV out.

The pipeline stops at the CSV on purpose. `stallkit listings push` takes it from
there, and that code already has tests behind it — so everything here sits *before*
the tested boundary rather than inside it, and a seller who would rather work in a
spreadsheet can edit the file and get an identical result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import csvio
from ..client import EtsyClient
from ..config import MAX_LISTING_IMAGES
from ..errors import ValidationError
from ..listings import LISTING_COLUMNS
from ..seo import MarketReport, research
from . import cache, generate, mockup, seeds
from .template import Template
from .workspace import Workspace

REVIEW_FILE = "review.csv"

# Columns the review file carries beyond what `listings push` reads. push() ignores
# extras, so the same file serves both the seller's eye and the writer.
REVIEW_EXTRA_COLUMNS = ["source_file", "concept", "evidence", "warnings"]


@dataclass
class DropRow:
    source: Path
    seed: seeds.Seed
    title: str = ""
    tags: list[str] = field(default_factory=list)
    description: str = ""
    images: list[Path] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return not self.skipped and bool(self.title) and bool(self.images)


@dataclass
class DropReport:
    batch: str
    out_dir: Path
    csv_path: Path | None = None
    rows: list[DropRow] = field(default_factory=list)
    concepts: int = 0
    researched: int = 0
    cached: int = 0

    @property
    def ready(self) -> list[DropRow]:
        return [r for r in self.rows if r.ok]

    @property
    def skipped(self) -> list[DropRow]:
        return [r for r in self.rows if not r.ok]

    @property
    def images_made(self) -> int:
        return sum(len(r.images) for r in self.rows)


def estimate_requests(products: int, concepts: int, images_per_product: int) -> int:
    """What a run will cost against the daily allowance, before it starts.

    A Personal Access app gets 5,000 requests a day. Research pages at 100 listings
    each, then every product costs one create plus one upload per image.
    """
    research_calls = concepts * 2  # a 200-listing sample is two pages of 100
    write_calls = products * (1 + images_per_product)
    return research_calls + write_calls


def _batch_name() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S-%f")


def _output_name(source: Path, template_image: Path | None, taken: set[str]) -> str:
    """A composite filename that cannot quietly land on another product's.

    Built from `Path.stem` alone, `mug.png` and `mug.jpg` — or, on Windows, `Mug.png`
    and `mug.png` — would produce one path. The second compose() call would overwrite
    the first, and with both rows pointing at that same path the report, the CSV and
    the image-existence check would all agree nothing was wrong while two listings
    shipped the same pictures. Readable names are kept for the ordinary case and only
    a real collision gets a suffix.
    """
    base = f"{source.stem}--{template_image.stem if template_image is not None else 'flat'}"
    candidate = f"{base}.jpg"
    attempt = 1
    while candidate.casefold() in taken:
        attempt += 1
        candidate = f"{base}-{attempt}.jpg"
    taken.add(candidate.casefold())
    return candidate


def _research_concept(
    client: EtsyClient | None, concept: str, *, sample: int, use_cache: bool
) -> tuple[MarketReport | None, bool]:
    """Returns (report, came_from_cache)."""
    key = f"{concept}|{sample}"
    if use_cache:
        cached = cache.load(key)
        if cached is not None:
            return MarketReport(**cached), True
    if client is None:
        return None, False
    report = research(client, concept, sample=sample)
    if use_cache:
        cache.store(key, report.__dict__)
    return report, False


def run(
    workspace: Workspace,
    template: Template,
    *,
    client: EtsyClient | None = None,
    mockups_per_product: int = 5,
    include_flat: bool = True,
    sample: int = 200,
    use_cache: bool = True,
    on_progress: Callable[[str], None] | None = None,
    exclude_products: set[str] | None = None,
) -> DropReport:
    """Composite, research, write copy, and emit review.csv. Nothing is sent to Etsy."""
    workspace.require()

    def say(message: str) -> None:
        if on_progress:
            on_progress(message)

    groups = [
        (path, images) for path, images in workspace.product_groups()
        if path.name.casefold() not in {name.casefold() for name in exclude_products or ()}
    ]
    report = DropReport(batch=_batch_name(), out_dir=workspace.drafts / _batch_name())
    report.out_dir = workspace.drafts / report.batch

    if not groups:
        return report

    available = workspace.mockup_files()
    mockups = available[:mockups_per_product]
    # A mockup left out by --mockups is never silently dropped: the row says so.
    unused = len(available) - len(mockups)

    # A composited row carries one image per mockup plus the flat render, and Etsy takes
    # ten per listing. Counted against the mockups that actually exist rather than the
    # number asked for, so `--mockups 50` on a workspace holding three is still a fine
    # run. Refused here, before a single image is composited or a single research call is
    # spent, because the alternative is a whole batch of review rows that `listings push`
    # is then obliged to reject one by one.
    planned = len(mockups) + (1 if include_flat else 0)
    if planned > MAX_LISTING_IMAGES:
        allowed = MAX_LISTING_IMAGES - (1 if include_flat else 0)
        flat_note = " plus the flat render" if include_flat else ""
        remedy = f"Use --mockups {allowed} or fewer" + (", or --no-flat." if include_flat else ".")
        raise ValidationError(
            f"{len(mockups)} mockup(s){flat_note} is {planned} images per listing, and "
            f"Etsy allows {MAX_LISTING_IMAGES}. {remedy}"
        )

    positions = mockup.load_positions(workspace.positions_path)
    # One calibration covers every mockup of the same size — that is the point of
    # storing fractions. A mockup with no entry of its own borrows the area of a
    # calibrated one with identical dimensions before falling back to the default.
    sizes = mockup.mockup_sizes(available)
    by_size: dict[tuple[int, int], mockup.PrintArea] = {}
    for name in sorted(positions):
        if name in sizes:
            by_size.setdefault(sizes[name], positions[name])

    def area_for(template_image: Path) -> mockup.PrintArea:
        own = positions.get(template_image.name)
        if own is not None:
            return own
        return by_size.get(sizes.get(template_image.name, (0, 0)), mockup.DEFAULT_PRINT_AREA)

    # The folder name is a useful fallback for `2-PRODUCTS/mountain sunset/IMG_01.png`,
    # but never for a file sitting directly in 2-PRODUCTS — that would turn the
    # workspace's own structural folder into a product concept.
    rows = [
        DropRow(
            source=path,
            seed=seeds.derive(
                path / "IMG_0001.jpg" if images else path,
                folder_fallback=bool(images) or path.parent != workspace.products,
            ),
            images=images,
        )
        for path, images in groups
    ]
    grouped = seeds.group([r.seed for r in rows])
    report.concepts = len(grouped)

    # Output names are handed out from one set per batch, so a collision between two
    # products is resolved rather than discovered later as a missing image.
    taken: set[str] = set()

    # One lookup per distinct concept, not per file. Eighteen concepts across a
    # hundred products is eighteen searches.
    reports: dict[str, MarketReport | None] = {}
    for concept in grouped:
        market, from_cache = _research_concept(client, concept, sample=sample, use_cache=use_cache)
        reports[concept] = market
        if from_cache:
            report.cached += 1
        elif market is not None:
            report.researched += 1
        say(f"researched {concept!r}" + (" (cached)" if from_cache else ""))

    for row in rows:
        if len(row.images) > MAX_LISTING_IMAGES:
            row.skipped = True
            row.warnings.append(
                f"product folder has more than {MAX_LISTING_IMAGES} images; "
                "nothing was truncated"
            )
            continue
        if not row.seed:
            row.skipped = True
            row.warnings.append(row.seed.reason or "no concept could be read from the filename")
            say(f"skipped {row.source.name}")
            continue

        copy = generate.generate(
            row.seed,
            reports.get(row.seed.text),
            template_description=template.description,
            fallback_tags=template.tags,
        )
        row.title, row.tags = copy.title, copy.tags
        row.description = copy.description
        row.evidence, row.warnings = copy.sources, list(copy.warnings)

        # Only the routes that redraw pixels turn a photo upright (see the EXIF note on
        # mockup.ORIENTATION_TAG). A ready photo is uploaded byte for byte, and Etsy
        # honours its orientation tag exactly as Explorer does, so rewriting it here
        # would spend a JPEG generation — and any transparency — to reach the same result.
        if row.source.is_dir():
            # Explicit ready-photo input: never composite or flatten these files. A
            # transparent file in here is still uploaded as it is, which is a listing
            # image with a black hole in it once Etsy flattens it — so say so rather
            # than quietly shipping it.
            bare = [image.name for image in row.images if mockup.looks_like_artwork(image)]
            if bare:
                row.warnings.append(
                    f"{len(bare)} transparent file(s) in this folder are uploaded as they "
                    f"are, not composited onto a mockup ({', '.join(bare[:3])}). Move them "
                    f"to 2-PRODUCTS as loose designs if they are artwork."
                )
        elif not mockup.looks_like_artwork(row.source):
            # A finished product photo needs no compositing; use it as it is.
            row.images = [row.source]
        else:
            if unused:
                row.warnings.append(
                    f"{unused} mockup(s) in 1-MOCKUPS were not used (--mockups "
                    f"{mockups_per_product}); raise it to include them, up to Etsy's "
                    f"{MAX_LISTING_IMAGES} images per listing"
                )
            for template_image in mockups:
                area = area_for(template_image)
                out = report.out_dir / _output_name(row.source, template_image, taken)
                try:
                    row.images.append(
                        mockup.compose(row.source, template_image, out, area=area)
                    )
                except Exception as exc:  # noqa: BLE001 — one bad file must not stop a batch
                    row.warnings.append(f"mockup {template_image.name} failed: {exc}")
            if include_flat:
                flat = report.out_dir / _output_name(row.source, None, taken)
                try:
                    row.images.append(mockup.flatten_design(row.source, flat))
                except Exception as exc:  # noqa: BLE001
                    row.warnings.append(f"flat render failed: {exc}")

        # Ready photos go to Etsy unchanged, so a format it refuses has to be handled
        # here rather than discovered after the draft exists. Composited images are
        # already JPEG, so for them every call below returns the path untouched. One
        # folder per product, because two products can both hold `01-front.webp`.
        convert_dir = report.out_dir / (
            row.source.name if row.source.is_dir() else row.source.stem
        )
        uploadable: list[Path] = []
        for image in row.images:
            try:
                converted = mockup.to_uploadable(image, convert_dir)
            except Exception as exc:  # noqa: BLE001 — one bad file must not stop a batch
                row.warnings.append(f"{image.name} could not be converted for Etsy: {exc}")
                continue
            if converted != image:
                row.warnings.append(
                    f"{image.name} was converted to {converted.name}: Etsy accepts only "
                    "JPG, PNG and GIF listing images"
                )
            uploadable.append(converted)
        row.images = uploadable

        if not row.images:
            row.skipped = True
            row.warnings.append(
                "no images produced — put at least one mockup in 1-MOCKUPS, "
                "or drop a finished product photo instead of transparent artwork"
            )
        say(f"prepared {row.source.name}")

    report.rows = rows
    if report.ready:
        report.csv_path = report.out_dir / REVIEW_FILE
        csvio.write_rows(
            report.csv_path,
            [_to_csv_row(r, template, report.csv_path.parent) for r in report.ready],
            columns=LISTING_COLUMNS + REVIEW_EXTRA_COLUMNS,
        )
    return report


def _to_csv_row(row: DropRow, template: Template, base: Path) -> dict[str, Any]:
    """One row in exactly the shape `stallkit listings push` consumes.

    `listing_id` is left empty, always. That is what makes this pipeline structurally
    incapable of updating or publishing an existing listing: Etsy only accepts a state
    change on an update, and there is never an id here to update.
    """
    data: dict[str, Any] = dict(template.fields)
    data.update(
        {
            "listing_id": "",
            "title": row.title,
            "description": row.description,
            "tags": row.tags,
            "materials": template.materials,
            "state": "",
            "images": [_relative(p, base) for p in row.images],
            "source_file": row.source.name,
            "concept": row.seed.text,
            "evidence": "; ".join(row.evidence),
            "warnings": "; ".join(row.warnings),
        }
    )
    return data


def _relative(path: Path, base: Path) -> str:
    """push() resolves image paths against the CSV's own folder.

    Ready photos live in 2-PRODUCTS, outside the batch folder, so a plain
    relative_to() fails for them, and an absolute path breaks the moment the
    workspace moves or syncs to another machine. `..` segments keep them
    relative; forward slashes keep the file readable on every platform. Only a path
    on another drive, which cannot be relative, stays absolute.
    """
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        return path.as_posix()
