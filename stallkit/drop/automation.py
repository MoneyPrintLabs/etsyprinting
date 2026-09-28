"""Prepare product folders and upload drafts with a durable duplicate guard.

The history and lock helpers below are shared with `drop.stream` (the web UI's
streaming run), so both writers keep one upload-history.json per workspace, keyed by
shop and product name, and never run at the same time.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from .. import csvio, listings
from ..client import EtsyClient
from ..config import LISTING_TYPES
from ..errors import ValidationError
from . import catalog, pipeline
from .template import Template
from .workspace import Workspace

log = logging.getLogger("stallkit.drop")


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


# One process writes the history while its own request threads read it (the pending
# list, the status check, İlanlar). Every read and write in this process goes through
# this lock, so a reader never holds the file open while the writer replaces it.
_HISTORY_LOCK = threading.RLock()

# On Windows a file cannot be replaced while anyone has it open: another program (an
# antivirus scan, OneDrive, Explorer's preview, a second stallkit) reading
# upload-history.json makes os.replace fail with "Access denied" for a moment. The
# write is retried with growing pauses, about 5 s in all, before anything gives up.
REPLACE_TRIES = 25
REPLACE_FIRST_PAUSE = 0.01
REPLACE_MAX_PAUSE = 0.3


def _busy(exc: OSError) -> bool:
    """A file another program holds open for a moment (Windows sharing violations)."""
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32, 33)


def _replace(temporary: Path, path: Path) -> None:
    """os.replace, retried while another program holds `path` open (Windows)."""
    pause = REPLACE_FIRST_PAUSE
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(temporary, path)
            return
        except OSError as exc:
            if not _busy(exc) or attempt == REPLACE_TRIES - 1:
                raise
        time.sleep(pause)
        pause = min(REPLACE_MAX_PAUSE, pause * 2)


def _write_in_place(path: Path, text: str) -> None:
    """The last resort when the file cannot be replaced: overwrite it where it is.

    Not atomic, but a program that only reads the file still lets it be written, and
    a draft's record on disk beats a draft Etsy has and the history does not.
    """
    with path.open("r+" if path.exists() else "w", encoding="utf-8") as handle:
        handle.seek(0)
        handle.write(text)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


def save_history(path: Path, state: dict) -> None:
    """Write the history atomically, or stop: an unsaved entry is a future duplicate."""
    text = json.dumps(state, ensure_ascii=False, indent=2)
    temporary = path.with_suffix(".tmp")
    with _HISTORY_LOCK:
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            _replace(temporary, path)
            return
        except OSError as exc:
            if not _busy(exc):
                raise ValidationError(
                    "Cannot save upload history; stopped to avoid duplicates."
                ) from exc
            log.warning("upload-history.json stayed busy (%s); writing it in place", exc)
        try:
            _write_in_place(path, text)
        except OSError as exc:
            raise ValidationError(
                "Cannot save upload history; stopped to avoid duplicates."
            ) from exc
        try:
            temporary.unlink()
        except OSError:
            pass


# --- the upload lock -------------------------------------------------------------------


def _lock_text(token: str) -> str:
    # The first line stays the bare PID: older stallkit versions wrote only that.
    return f"{os.getpid()}\n{socket.gethostname()}\n{token}\n"


def _read_lock(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return None
    except OSError:
        return []


@contextmanager
def upload_lock(root: Path):
    """One writer per workspace. Raises UploadLocked while another run holds it."""
    path = lock_path(root)
    token = secrets.token_hex(8)
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
            handle.write(_lock_text(token))
        yield
    finally:
        # Removed only while it is still this run's lock. Someone may have deleted it
        # by hand during the run (the workspace README says how, for a crashed run);
        # that must not turn a finished run into an error, and a lock another run took
        # since then is that run's to remove.
        lines = _read_lock(path)
        if lines is None:
            log.warning("the upload lock %s was removed while this run held it", path)
        elif len(lines) >= 3 and lines[2].strip() == token:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("could not remove the upload lock %s: %s", path, exc)
        else:
            log.warning("the upload lock %s now belongs to another run; left in place", path)


def _pid_alive(pid: int) -> bool | None:
    """Whether a process with this id runs on this computer (None: cannot tell)."""
    if pid <= 0:
        return None
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        query_limited_information, still_active = 0x1000, 259
        handle = kernel32.OpenProcess(query_limited_information, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 5:  # access denied: it exists, it is someone else's
                return True
            if error == 87:  # invalid parameter: no such process
                return False
            return None
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # signal 0 only asks; on Windows it would not (see above)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def lock_info(root: Path) -> dict[str, Any] | None:
    """What the workspace's upload lock says, or None when there is no lock.

    {"pid": int | None, "since": epoch | None, "alive": bool | None, "stale": bool}.
    `stale` is True only when the lock was written on this computer and its process
    provably no longer runs: a crashed run. A PID the system has since handed to
    another program reads as alive, so a stale lock can look active, never the reverse.
    """
    path = lock_path(root)
    lines = _read_lock(path)
    if lines is None:
        return None
    try:
        since: float | None = path.stat().st_mtime
    except OSError:
        since = None
    try:
        pid: int | None = int(lines[0].strip()) if lines else None
    except ValueError:
        pid = None
    host = lines[1].strip() if len(lines) > 1 else ""
    alive = _pid_alive(pid) if pid is not None else None
    # A lock from another computer (a synced folder) says nothing about processes here.
    same_host = not host or host == socket.gethostname()
    return {"pid": pid, "since": since, "alive": alive if same_host else None,
            "stale": bool(same_host and alive is False)}


# --- reading the history ------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    """The file's text, retried while another program holds it (None: no file)."""
    pause = REPLACE_FIRST_PAUSE
    for attempt in range(REPLACE_TRIES):
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            if not _busy(exc) or attempt == REPLACE_TRIES - 1:
                raise
        time.sleep(pause)
        pause = min(REPLACE_MAX_PAUSE, pause * 2)
    return None  # pragma: no cover - the loop always returns or raises


# For the app's other small state files (last-run.json) that readers open meanwhile.
replace_file = _replace
read_text = _read_text


def load_history(path: Path) -> dict:
    """The whole history file ({shop: {product: entry}}); {} when there is none yet."""
    with _HISTORY_LOCK:
        try:
            text = _read_text(path)
            state = json.loads(text) if text is not None else {}
            if not isinstance(state, dict) or any(not isinstance(v, dict) for v in state.values()):
                raise ValueError("invalid history")
        except (ValueError, OSError) as exc:
            raise ValidationError(
                "Cannot read upload-history.json; stopped to avoid duplicates."
            ) from exc
    return state


def read_history(root: Path) -> dict:
    """The history for a screen that only shows it: {} when it cannot be read.

    Same lock as the writer, so a reader in this process never makes a save fail.
    """
    try:
        return load_history(history_path(root))
    except ValidationError:
        return {}


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

    def _progress_saved(self) -> None:
        # The draft id is already on disk; a count of images or variations is only
        # progress, and the product's final save records it. A save that fails here
        # must not cost the draft its remaining images.
        try:
            save_history(self.path, self.state)
        except ValidationError as exc:
            log.warning("could not record progress in upload-history.json: %s", exc.__cause__)

    def update_listing_inventory(self, listing_id, inventory):
        result = self.client.update_listing_inventory(listing_id, inventory)
        self.entry["variations"] = len(inventory["products"])
        self._progress_saved()
        return result

    def upload_listing_image(self, listing_id, image, *, rank, alt_text=""):
        # alt_text only when there is one: a client that takes no alt text still works.
        extra = {"alt_text": alt_text} if alt_text else {}
        result = self.client.upload_listing_image(listing_id, image, rank=rank, **extra)
        self.entry["images_uploaded"] = rank
        # Which file each picture on the draft is: a mockup's name says its product and
        # colour, which the listing page shows on the picture. Keyed by Etsy's image id,
        # so a picture moved in Etsy keeps its own name.
        image_id = result.get("listing_image_id") if isinstance(result, dict) else None
        if image_id:
            self.entry.setdefault("images", {})[str(image_id)] = Path(image).name
        self._progress_saved()
        return result

    def upload_listing_file(self, listing_id, path, *, rank):
        result = self.client.upload_listing_file(listing_id, path, rank=rank)
        self.entry["files_uploaded"] = rank
        self._progress_saved()
        return result


_RecordedClient = RecordedClient


def run(workspace: Workspace, template: Template, *, client: EtsyClient | None = None,
        dry_run: bool = False, mockups: Sequence[Path] | None = None) -> AutoReport:
    """Run once. Existing or uncertain products are never automatically recreated.

    The local history is scoped to a shop and product path. An interrupted POST
    cannot be retried safely, so pending/partial/error entries need manual review.
    The template's type is kept: `physical`, or `download` / `both`, whose drafts also
    get each product's download files after its images (a loose design's original file;
    a folder's `dosyalar` / `files` subfolder). A product without a download it can send
    is skipped by the pipeline, which stops the batch before anything is uploaded.
    `mockups` are the templates to composite onto, first (the main image) to last;
    by default the ones chosen on the Mockuplar page, in that order
    (`catalog.enabled_mockups`), exactly as the app's own runs use them.
    """
    workspace.require()
    if mockups is None:
        mockups = catalog.enabled_mockups(workspace)
    if client is None and not dry_run:
        raise ValidationError("Connect your Etsy shop before uploading drafts.")
    listing_type = template.fields.get("type") or "physical"
    if listing_type not in LISTING_TYPES:
        raise ValidationError(
            f"The template listing's type {listing_type!r} is not one Etsy knows "
            f"({', '.join(LISTING_TYPES)}). Pick the template listing again."
        )
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
        prepared = pipeline.run(workspace, template, client=client, exclude_products=set(history),
                                mockups=mockups)
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
                     "files_uploaded": 0, "review_csv": str(prepared.csv_path)}
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
