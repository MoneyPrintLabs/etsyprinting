"""Prepare product folders and upload drafts with a durable duplicate guard.

The history and lock helpers below are shared with `drop.stream` (the web UI's
streaming run), so both writers keep one upload-history.json per workspace, keyed by
shop and product name, and never run at the same time.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .. import csvio, listings
from ..client import EtsyClient
from ..errors import ValidationError
from . import pipeline
from .template import Template
from .workspace import Workspace


@dataclass
class AutoReport:
    prepared: pipeline.DropReport | None = None
    uploaded: listings.PushReport = field(default_factory=listings.PushReport)
    already_done: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)


HISTORY_FILE = "upload-history.json"
LOCK_FILE = ".auto-upload.lock"


class UploadLocked(ValidationError):
    """Another run holds the workspace's upload lock (or a crashed one left it behind)."""

    def __init__(self, message: str, path: Path) -> None:
        super().__init__(message)
        self.path = path


def history_path(root: Path) -> Path:
    return root / HISTORY_FILE


def lock_path(root: Path) -> Path:
    return root / LOCK_FILE


def save_history(path: Path, state: dict) -> None:
    """Write the history atomically, or stop: an unsaved entry is a future duplicate."""
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except OSError as exc:
        raise ValidationError("Cannot save upload history; stopped to avoid duplicates.") from exc


@contextmanager
def upload_lock(root: Path):
    """One writer per workspace. Raises UploadLocked while another run holds it."""
    path = lock_path(root)
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise UploadLocked(
            f"Another auto run may be active. If it crashed, stop that process, "
            f"then remove {path}. Keep upload-history.json.",
            path,
        ) from exc
    try:
        with handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def load_history(path: Path) -> dict:
    """The whole history file ({shop: {product: entry}}); {} when there is none yet."""
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(state, dict) or any(not isinstance(v, dict) for v in state.values()):
            raise ValueError("invalid history")
    except (ValueError, OSError) as exc:
        raise ValidationError("Cannot read upload-history.json; stopped to avoid duplicates.") from exc
    return state


def shop_history(state: dict, shop: str | None) -> dict:
    """The entries that guard `shop`; with no shop (a dry run), every shop's entries.

    For a real shop this is the section itself, not a copy, so entries added to it
    reach `state` once the caller stores it back under `state[shop]`.
    """
    if shop is None:
        # A dry run cannot know which shop it rehearses for, so it checks against
        # every shop in the file. Otherwise it reads an empty history, validates
        # products the real run will skip, and says nothing about a half-uploaded
        # draft that needs looking at — the one thing a rehearsal is for.
        history: dict = {}
        for section in state.values():
            if isinstance(section, dict):
                history.update(section)
    else:
        history = state.get(shop, {})
    if any(not isinstance(v, dict) or "status" not in v for v in history.values()):
        raise ValidationError("Invalid upload history; review it before continuing.")
    return history


def known_products(history: dict, names: set[str]) -> tuple[list[str], list[str]]:
    """(already done, needs review) among the entries whose product is still in the folder.

    `names` are the casefolded names of the products in 2-PRODUCTS: Windows folder
    names are case-insensitive, so `Mug` renamed to `mug` is still the same product.
    """
    already_done: list[str] = []
    needs_review: list[str] = []
    for name, entry in history.items():
        if name.casefold() in names:
            if entry["status"] == "ok":
                already_done.append(name)
            else:
                needs_review.append(
                    f"{name}: {entry['status']}, listing {entry.get('listing_id') or 'unknown'}"
                )
    return already_done, needs_review


# The names these helpers had before they were shared.
_save = save_history
_lock = upload_lock


class RecordedClient:
    """Save the draft id before uploading its first image."""

    def __init__(self, client, path, state, entry):
        self.client, self.path, self.state, self.entry = client, path, state, entry

    def create_draft_listing(self, fields):
        result = self.client.create_draft_listing(fields)
        raw_id = result.get("listing_id") if isinstance(result, dict) else None
        try:
            self.entry["listing_id"] = int(raw_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # Leave the entry pending: listings.push reports it, and a pending entry is
            # never retried automatically.
            pass
        save_history(self.path, self.state)
        return result

    def update_listing_inventory(self, listing_id, inventory):
        result = self.client.update_listing_inventory(listing_id, inventory)
        self.entry["variations"] = len(inventory["products"])
        save_history(self.path, self.state)
        return result

    def upload_listing_image(self, listing_id, image, *, rank):
        result = self.client.upload_listing_image(listing_id, image, rank=rank)
        self.entry["images_uploaded"] = rank
        save_history(self.path, self.state)
        return result


_RecordedClient = RecordedClient


def run(workspace: Workspace, template: Template, *, client: EtsyClient | None = None,
        dry_run: bool = False) -> AutoReport:
    """Run once. Existing or uncertain products are never automatically recreated.

    The local history is scoped to a shop and product path. An interrupted POST
    cannot be retried safely, so pending/partial/error entries need manual review.
    """
    workspace.require()
    if client is None and not dry_run:
        raise ValidationError("Connect your Etsy shop before uploading drafts.")
    if template.fields.get("type", "physical") != "physical":
        raise ValidationError("Automatic upload currently supports physical products only; digital delivery files are not supported.")
    with upload_lock(workspace.root):
        path = history_path(workspace.root)
        state = load_history(path)
        shop = str(client.shop_id()) if client is not None else None
        history = shop_history(state, shop)
        report = AutoReport()
        # Windows folder names are case-insensitive, so `Mug` renamed to `mug` is still
        # the same product; matching exactly would upload it a second time.
        names = {p.name.casefold() for p, _ in workspace.product_groups()}
        report.already_done, report.needs_review = known_products(history, names)
        prepared = pipeline.run(workspace, template, client=client, exclude_products=set(history))
        report.prepared = prepared
        if prepared.skipped:
            details = "; ".join(f"{row.source.name}: {', '.join(row.warnings)}" for row in prepared.skipped)
            raise ValidationError(f"Nothing uploaded; fix skipped products: {details}")
        if prepared.csv_path is None:
            return report
        rows = csvio.read_rows(prepared.csv_path)
        checks = listings.push(None, rows, base_dir=prepared.csv_path.parent, dry_run=True)
        if checks.errors:
            raise ValidationError("Nothing uploaded: " + "; ".join(r.message for r in checks.results if r.failed))
        # Decode every image before creating the first draft, not halfway through a
        # batch. Image.verify() is not enough: Pillow's base verify() is a no-op and
        # only PNG overrides it, so a JPEG cut short by a half-finished copy would pass
        # and then fail on upload, after the draft already existed. load() is a real
        # decode, and it costs nothing beside the upload that follows it.
        for row in prepared.ready:
            for image in row.images:
                try:
                    with Image.open(image) as opened:
                        opened.load()
                except (OSError, ValueError) as exc:
                    raise ValidationError(f"Invalid image {image.name}; nothing uploaded.") from exc
        if dry_run:
            report.uploaded = checks
            return report
        # The template listing's options travel with its fields. A template without
        # variations has nothing to add, and its single price is already on the row.
        inventory = None
        if template.source_listing_id:
            source = listings.inventory_for_copy(
                client.listing_inventory(template.source_listing_id)
            )
            inventory = source if listings.has_variations(source) else None
        state[shop] = history
        # One row is pushed at a time, so push() would number every result "row 2";
        # the number is set to the product's real line in review.csv instead.
        for line, (product, row) in enumerate(zip(prepared.ready, rows), start=2):
            entry = {"status": "pending", "listing_id": None, "images_uploaded": 0,
                     "review_csv": str(prepared.csv_path)}
            history[product.source.name] = entry
            # Persist intent BEFORE the request, including ambiguous network failures.
            save_history(path, state)
            recorder = RecordedClient(client, path, state, entry)
            result = listings.push(
                recorder, [row], base_dir=prepared.csv_path.parent, inventory=inventory
            ).results[0]
            result.row = line
            entry.update(status=result.status, message=result.message)
            save_history(path, state)
            report.uploaded.results.append(result)
        return report
