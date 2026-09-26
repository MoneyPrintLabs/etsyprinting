"""Tasarım Yükle: design uploads, the pending list, and the draft run.

    PUT    /api/designs/files?path=<name | folder/name>[&batch=<id>]   raw image body
    DELETE /api/designs/files?path=<name | folder | folder/name>       -> moved to archive/
    GET    /api/designs/pending        what the next run would do, and what blocks it
    POST   /api/designs/start          {"dry_run"?: bool} -> job "designs" (drop.stream)
    GET    /api/designs/last           the last finished run (3-DRAFTS/last-run.json)
    POST   /api/designs/review/forget  {"name", "confirm": true}: try a product again
    POST   /api/designs/unlock         {"confirm": true}: remove a crashed run's lock

A run creates DRAFTS only; nothing is published. The confirm modal in the page
("Başlat") is the consent for creating them.
"""

from __future__ import annotations

import collections
import io
import json
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..router import ApiError, Request

if TYPE_CHECKING:  # pragma: no cover
    from ..context import AppContext
    from ..jobs import Job
    from ..router import Router

log = logging.getLogger("stallkit.web")

KIND = "designs"
CONCURRENCY = 3
MAX_UPLOAD = 50 * 1024 * 1024
LAST_RUN_FILE = "last-run.json"
PRODUCTS_PREFIX = "2-PRODUCTS"
# The status states that stop a real run before it starts (a dry run needs none of them).
_STATE_BLOCKERS = {
    "keys": "keys",
    "bad_keys": "bad_keys",
    "disconnected": "connect",
    "reconnect": "reconnect",
    "offline": "offline",
}
_ETSY_BLOCKERS = set(_STATE_BLOCKERS.values())
# At most this often the whole job state is copied for GET /api/jobs/{id}.
STATE_INTERVAL = 0.5

# Checking "is a run active?" and starting one happen together, so a double click
# cannot queue a second run behind the first.
_start_lock = threading.Lock()
_claims_lock = threading.Lock()
# (products folder, upload batch, folder name) -> the folder that batch writes into.
_claims: collections.OrderedDict[tuple[str, str, str], str] = collections.OrderedDict()
_KEEP_CLAIMS = 2000


def register(r: Router, ctx: AppContext) -> None:
    r.put("/api/designs/files", upload_file)
    r.delete("/api/designs/files", delete_file)
    r.get("/api/designs/pending", pending)
    r.post("/api/designs/start", start)
    r.get("/api/designs/last", last_run)
    r.post("/api/designs/review/forget", forget)
    r.post("/api/designs/unlock", unlock)


def _ctx(req: Request) -> AppContext:
    assert req.ctx is not None
    return req.ctx


def _active_job(ctx: AppContext) -> Job | None:
    jobs = ctx.jobs.list(kind=KIND, active=True)
    return jobs[0] if jobs else None


def _rel(ws: Any, path: Path) -> str:
    try:
        return path.resolve().relative_to(ws.root.resolve()).as_posix()
    except (OSError, ValueError):
        return ""


# --- file names -------------------------------------------------------------------------

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
             *(f"lpt{i}" for i in range(10))}


def _safe_segment(text: str, default: str) -> str:
    """One file or folder name that is safe on Windows and macOS."""
    text = unicodedata.normalize("NFC", str(text or ""))
    text = _UNSAFE.sub("", text).strip().strip(".").strip()
    if len(text) > 120:
        stem, dot, suffix = text.rpartition(".")
        text = f"{stem[:110].rstrip()}.{suffix}" if dot and len(suffix) <= 5 else text[:120]
    if not text:
        text = default
    if Path(text).stem.lower() in _RESERVED:
        text = f"{default}-{text}"
    return text


def _split_path(raw: str) -> list[str]:
    parts = [p for p in re.split(r"[\\/]+", raw or "") if p not in ("", ".")]
    if not parts or len(parts) > 2 or any(p == ".." for p in parts):
        raise ApiError(422, "invalid", "path must be <name> or <folder>/<name>", field="path")
    if any(p.startswith(".") for p in parts):
        raise ApiError(422, "invalid", "hidden files are not designs", field="path")
    return parts


def _entry_ci(folder: Path, name: str) -> Path | None:
    """The entry of `folder` called `name`, ignoring case (as Windows and macOS do)."""
    wanted = name.casefold()
    try:
        for entry in folder.iterdir():
            if entry.name.casefold() == wanted:
                return entry
    except OSError:
        return None
    return None


def _claim_folder(products: Path, folder: str, batch: str) -> str:
    """The product folder an upload batch writes `folder` into.

    Within one batch every file of a dropped folder lands in the same place. A folder
    that already exists from before gets a new name (`-2`), so a new product is never
    merged into an old one — which the history would then skip as already uploaded.
    """
    key = (str(products), batch, folder.casefold())
    with _claims_lock:
        if batch and key in _claims:
            return _claims[key]
        name, number = folder, 1
        while batch and _entry_ci(products, name) is not None:
            number += 1
            name = f"{folder}-{number}"
        (products / name).mkdir(parents=True, exist_ok=True)
        if batch:
            _claims[key] = name
            while len(_claims) > _KEEP_CLAIMS:
                _claims.popitem(last=False)
        return name


def _save_unique(folder: Path, name: str, data: bytes) -> tuple[str, bool]:
    """Save without overwriting: `x.png`, then `x-2.png`, ... (name, was_already_there).

    The same bytes under the same name are the same design dropped twice: nothing new
    is written, so one design never becomes two listings.
    """
    folder.mkdir(parents=True, exist_ok=True)
    stem, suffix = Path(name).stem, Path(name).suffix
    number = 1
    while True:
        candidate = name if number == 1 else f"{stem}-{number}{suffix}"
        existing = _entry_ci(folder, candidate)
        if existing is not None:
            try:
                same = (existing.is_file() and existing.stat().st_size == len(data)
                        and existing.read_bytes() == data)
            except OSError:
                same = False
            if same:
                return existing.name, True
            number += 1
            continue
        try:
            with (folder / candidate).open("xb") as handle:
                handle.write(data)
        except FileExistsError:
            number += 1
            continue
        return candidate, False


# --- history ---------------------------------------------------------------------------------


def _shop_key(ctx: AppContext) -> str | None:
    shop = (ctx.status.get("shop") or {}).get("etsy_shop_id")
    return str(shop) if shop else None


def _history(ctx: AppContext, ws: Any) -> tuple[dict, str | None]:
    """(entries that guard the open shop, a problem code). Every shop's when unknown."""
    from ...drop import automation
    from ...errors import ValidationError

    try:
        state = automation.load_history(automation.history_path(ws.root))
        return automation.shop_history(state, _shop_key(ctx)), None
    except ValidationError:
        return {}, "history"


# --- upload / remove -------------------------------------------------------------------------


def upload_file(req: Request) -> dict[str, Any]:
    from PIL import Image

    from ...drop.workspace import IMAGE_SUFFIXES, PREVIEW_SUFFIXES

    ctx = _ctx(req)
    parts = _split_path(req.query.get("path", ""))
    data = req.body
    if not data:
        raise ApiError(422, "invalid", "The file is empty.", field="body")
    if len(data) > MAX_UPLOAD:
        raise ApiError(413, "too_large", "A design can be at most 50 MB.",
                       limit=MAX_UPLOAD, max_mb=MAX_UPLOAD // (1024 * 1024))
    name = _safe_segment(parts[-1], "design")
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ApiError(422, "not_image", f"{name} is not a PNG, JPG or other image file.",
                       name=name)
    name = f"{Path(name).stem}{suffix}"
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ApiError(422, "not_image", f"{name} cannot be read as an image ({exc}).",
                       name=name) from exc

    ws = ctx.workspace()
    folder = None
    if len(parts) == 2:
        batch = re.sub(r"[^A-Za-z0-9_-]", "", req.query.get("batch", ""))[:64]
        folder = _claim_folder(ws.products, _safe_segment(parts[0], "product"), batch)
    target = ws.products / folder if folder else ws.products
    saved, duplicate = _save_unique(target, name, data)
    product = folder or saved
    history, _problem = _history(ctx, ws)
    known = product.casefold() in {n.casefold() for n in history}
    ignored = None
    if not folder and any(Path(saved).stem.endswith(s) for s in PREVIEW_SUFFIXES):
        ignored = "preview_name"
    path = target / saved
    return {
        "name": product,
        "file": saved,
        "folder": folder,
        "path": _rel(ws, path),
        "duplicate": duplicate,
        "known": known,
        "ignored": ignored,
        "size": len(data),
    }


def delete_file(req: Request) -> dict[str, Any]:
    """Take a design (or a product folder, or one photo of it) out of the next run.

    It is moved to the workspace's archive/ folder, never deleted: the file may be the
    seller's only copy (2-PRODUCTS is also filled from Explorer or Finder).
    """
    from ...drop.workspace import IMAGE_SUFFIXES
    from .. import files

    ctx = _ctx(req)
    if _active_job(ctx) is not None:
        raise ApiError(409, "busy", "Wait for the running batch to finish first.")
    parts = _split_path(req.query.get("path", ""))
    ws = ctx.workspace()
    target = files.resolve_inside(ws.products, "/".join(parts))
    if target is None or not target.exists():
        raise ApiError(404, "not_found", "No such design in the products folder.")
    if target.is_file() and target.suffix.lower() not in IMAGE_SUFFIXES:
        raise ApiError(404, "not_found", "Only designs can be removed here.")
    if target.is_dir() and len(parts) != 1:
        raise ApiError(404, "not_found", "Only product folders can be removed here.")
    archive = ws.archive / parts[0] if len(parts) == 2 else ws.archive
    archive.mkdir(parents=True, exist_ok=True)
    stem, suffix = (target.stem, target.suffix) if target.is_file() else (target.name, "")
    destination = archive / target.name
    number = 1
    while destination.exists():
        number += 1
        destination = archive / f"{stem}-{number}{suffix}"
    shutil.move(str(target), str(destination))
    return {"ok": True, "archived": _rel(ws, destination)}


# --- what the next run would do ----------------------------------------------------------------


def _template_info(ctx: AppContext, ws: Any) -> tuple[dict[str, Any] | None, str | None]:
    """(template facts for the page, a blocker code)."""
    from ...drop import stream
    from ...drop.template import Template
    from ...errors import ValidationError

    if not ws.template_path.is_file():
        return None, "template"
    try:
        template = Template.from_dict(ws.read_template())
    except ValidationError as exc:
        return {"title": None, "invalid": str(exc)}, "template_invalid"
    fields = template.fields
    info: dict[str, Any] = {
        "title": template.source_title or None,
        "source_listing_id": template.source_listing_id,
        "price": fields.get("price"),
        "currency": (ctx.status.get("shop") or {}).get("currency"),
        "description": bool(template.description.strip()),
        "shipping_profile": bool(fields.get("shipping_profile_id")),
        "tags": len(template.tags),
        "invalid": None,
    }
    try:
        stream.check_template(template)
    except ValidationError as exc:
        info["invalid"] = str(exc)
        return info, "template_invalid"
    return info, None


def _pending_info(ctx: AppContext) -> dict[str, Any]:
    from ...config import MAX_LISTING_IMAGES
    from ...drop import automation, catalog, pipeline, seeds

    if not ctx.wait_first_status(0):
        ctx.set_status_soon(0.0)
        ctx.wait_first_status(3.0)
    status = ctx.status
    ws = ctx.workspace()
    history, history_problem = _history(ctx, ws)
    known = {name.casefold() for name in history}
    groups = ws.product_groups()
    items: list[dict[str, Any]] = []
    concepts: set[str] = set()
    for path, photos in groups:
        if path.name.casefold() in known:
            continue
        seed = seeds.derive(
            path / "IMG_0001.jpg" if photos else path,
            folder_fallback=bool(photos) or path.parent != ws.products,
        )
        if seed:
            concepts.add(seed.text)
        shown = photos[0] if photos else path
        try:
            stat = shown.stat()
            mtime, size = int(stat.st_mtime), stat.st_size
        except OSError:
            mtime, size = 0, 0
        items.append({
            "name": path.name,
            "kind": "folder" if photos else "design",
            "files": len(photos) if photos else 1,
            "thumb_path": _rel(ws, shown),
            "mtime": mtime,
            "size": size,
            "concept": seed.text if seed else "",
            "junk_reason": None if seed else (seed.reason or "junk"),
            "too_many": len(photos) > MAX_LISTING_IMAGES,
        })
    names = {path.name.casefold() for path, _ in groups}
    already_done, _ = automation.known_products(history, names)
    review = [
        {"name": name, "status": entry.get("status"), "listing_id": entry.get("listing_id"),
         "message": entry.get("message") or ""}
        for name, entry in history.items()
        if name.casefold() in names and entry.get("status") != "ok"
    ]

    infos = catalog.load(ws)
    enabled = catalog.enabled_mockups(ws)
    types = collections.Counter(infos[p.name].type for p in enabled if p.name in infos)
    template, template_problem = _template_info(ctx, ws)

    blockers: list[str] = []
    state = status.get("state")
    if state in _STATE_BLOCKERS:
        blockers.append(_STATE_BLOCKERS[state])
    if template_problem:
        blockers.append(template_problem)
    if history_problem:
        blockers.append(history_problem)
    if _active_job(ctx) is not None:
        blockers.append("running")
    elif automation.lock_path(ws.root).exists():
        blockers.append("locked")
    runnable = sum(1 for item in items if not item["junk_reason"] and not item["too_many"])
    if not items:
        blockers.append("empty")
    elif not runnable:
        blockers.append("junk_only")

    warnings: list[str] = []
    junk = sum(1 for item in items if item["junk_reason"] or item["too_many"])
    if junk:
        warnings.append("junk")
    if not enabled and any(item["kind"] == "design" for item in items):
        warnings.append("no_mockups")
    if template and not template.get("shipping_profile"):
        warnings.append("no_shipping_profile")
    images_each = len(enabled) + 1
    estimate = pipeline.estimate_requests(len(items), len(concepts), images_each)
    quota = status.get("quota_remaining")
    if isinstance(quota, int) and estimate > quota:
        warnings.append("quota")
    lock = automation.lock_path(ws.root)
    try:
        locked_at = int(lock.stat().st_mtime) if lock.exists() else None
    except OSError:
        locked_at = None

    shop = status.get("shop") or {}
    return {
        "items": items,
        "count": len(items),
        "runnable": runnable,
        "already_done": len(already_done),
        "review": review,
        "mockups": {
            "enabled": len(enabled),
            "total": len(infos),
            "types": dict(types),
            "primary": types.most_common(1)[0][0] if types else None,
        },
        "template": template,
        "shop": {"connected": state == "connected", "name": shop.get("name"),
                 "state": state},
        "workspace": str(ws.root),
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "estimate_requests": estimate,
        "quota_remaining": quota,
        "concepts": len(concepts),
        "images_each": images_each,
        "locked_at": locked_at,
        "concurrency": CONCURRENCY,
    }


def pending(req: Request) -> dict[str, Any]:
    return _pending_info(_ctx(req))


# --- the run -------------------------------------------------------------------------------------


class _JobCancel:
    def __init__(self, job: Job) -> None:
        self.job = job

    def is_set(self) -> bool:
        return self.job.cancelled


def _new_item(data: dict[str, Any]) -> dict[str, Any]:
    from ...drop.stream import STEPS

    # Every key exists from the start: the dicts are read while other threads write.
    return {
        "index": data["index"],
        "name": data["name"],
        "kind": data.get("kind", "design"),
        "thumb_path": data.get("source", ""),
        "files": data.get("files", 1),
        "status": "queued",
        "step": None,
        "steps": {step: "todo" for step in STEPS},
        "mode": "",
        "title": "",
        "tags": [],
        "images": [],
        "listing_id": None,
        "images_uploaded": 0,
        "images_total": 0,
        "sampled": None,
        "warnings": [],
        "error": None,
        "updated_at": time.time(),
    }


def _copy_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out["steps"] = dict(item["steps"])
    out["tags"] = list(item["tags"])
    out["images"] = list(item["images"])
    out["warnings"] = list(item["warnings"])
    return out


class _Tracker:
    """Turns drop.stream events into job state, SSE item events and progress."""

    def __init__(self, job: Job, *, dry_run: bool, template: dict[str, Any] | None,
                 mockups: dict[str, Any]) -> None:
        self.job = job
        self.lock = threading.RLock()
        self.items: list[dict[str, Any]] = []
        self.started = time.time()
        self.state: dict[str, Any] = {
            "total": 0, "done": 0, "created": 0, "errors": 0, "warnings": 0, "checked": 0,
            "cancelled_items": 0, "started_at": self.started, "finished_at": None,
            "items": [], "current": None, "dry_run": dry_run, "batch": None,
            "out_dir": None, "template": template, "mockups": mockups,
            "concurrency": CONCURRENCY, "already_done": 0, "needs_review": [],
            "result": None,
        }
        self._last_state = 0.0
        self.publish_state(force=True)

    # the job state other requests read: a copy, refreshed at most every STATE_INTERVAL
    def publish_state(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_state < STATE_INTERVAL:
            return
        self._last_state = now
        snapshot = dict(self.state)
        snapshot["items"] = [_copy_item(item) for item in self.items]
        self.job.set_state(**snapshot)

    def counts(self) -> dict[str, Any]:
        return {key: self.state[key] for key in (
            "total", "done", "created", "errors", "warnings", "checked", "cancelled_items",
            "current")}

    def _current(self) -> int | None:
        """The product the "being prepared" card follows: the first one still in steps 1-4,
        else the one being created."""
        for item in self.items:
            if item["status"] == "running" and item["step"] in ("mockup", "research", "title",
                                                                 "tags"):
                return item["index"]
        for item in self.items:
            if item["status"] == "running":
                return item["index"]
        return None

    def on_event(self, name: str, step: str, status: str, data: dict[str, Any]) -> None:
        from ...drop.stream import FINAL, STEPS

        with self.lock:
            if step == "batch":
                self.items = [_new_item(entry) for entry in data.get("items", [])]
                self.state.update(
                    total=len(self.items), batch=data.get("batch"), out_dir=data.get("out_dir"),
                    already_done=len(data.get("already_done") or []),
                    needs_review=list(data.get("needs_review") or []),
                )
                self.job.progress(0, len(self.items))
                self.publish_state(force=True)
                self.job.emit("batch", state=self.counts(),
                              items=[_copy_item(item) for item in self.items],
                              batch=data.get("batch"))
                return
            index = data.get("index")
            if not isinstance(index, int) or not 0 <= index < len(self.items):
                return
            item = self.items[index]
            for key in ("images", "mode", "title", "tags", "listing_id", "images_uploaded",
                        "images_total", "sampled"):
                if key in data and data[key] is not None:
                    item[key] = list(data[key]) if isinstance(data[key], list) else data[key]
            if step in STEPS:
                item["steps"][step] = status
                if status == "running":
                    item["status"] = "running"
                    item["step"] = step
            elif step == "item":
                was_final = item["status"] in FINAL
                item["status"] = status
                item["step"] = None
                if isinstance(data.get("steps"), dict):
                    item["steps"].update(data["steps"])
                item["warnings"] = list(data.get("warnings") or [])
                item["error"] = data.get("problem")
                if status in FINAL and not was_final:
                    self.state["done"] += 1
                    if status in ("ok", "partial"):
                        self.state["created"] += 1
                    elif status == "error":
                        self.state["errors"] += 1
                    elif status == "checked":
                        self.state["checked"] += 1
                    elif status == "cancelled":
                        self.state["cancelled_items"] += 1
                    self.state["warnings"] += len(item["warnings"])
            item["updated_at"] = time.time()
            self.state["current"] = self._current()
            final = step == "item" and status in FINAL
            if final:
                current = self.state["current"]
                label = self.items[current]["name"] if current is not None else item["name"]
                self.job.progress(self.state["done"], self.state["total"], label=label)
            self.job.emit("item", item=_copy_item(item), state=self.counts())
            self.publish_state(force=final and self.state["done"] >= self.state["total"])

    def abort(self) -> None:
        """The run broke off: nothing still waiting will happen now."""
        from ...drop.stream import FINAL

        with self.lock:
            for item in self.items:
                if item["status"] not in FINAL:
                    item["status"] = "cancelled"
                    item["step"] = None
                    item["steps"] = {k: ("todo" if v == "running" else v)
                                     for k, v in item["steps"].items()}
                    self.state["done"] += 1
                    self.state["cancelled_items"] += 1
            self.state["finished_at"] = time.time()
            self.publish_state(force=True)

    def finish(self, report: Any, ws: Any) -> dict[str, Any]:
        with self.lock:
            finished = report.finished_at or time.time()
            summary = {
                "batch": report.batch,
                "dry_run": report.dry_run,
                "total": len(report.items),
                "created": report.created,
                "ok": report.count("ok"),
                "partial": report.count("partial"),
                "errors": report.errors,
                "checked": report.count("checked"),
                "cancelled_items": report.count("cancelled"),
                "warnings": report.warnings,
                "cancelled": report.cancelled,
                "stopped": report.stopped.to_dict() if report.stopped else None,
                "started_at": self.started,
                "finished_at": finished,
                "duration": round(finished - self.started, 1),
                "csv": _rel(ws, report.csv_path) if report.csv_path else None,
                "out_dir": _rel(ws, report.out_dir) if report.out_dir.exists() else None,
                "already_done": len(report.already_done),
                "needs_review": list(report.needs_review),
                "researched": report.researched,
                "cached": report.cached,
            }
            self.state.update(finished_at=finished, result=summary)
            self.publish_state(force=True)
            return summary


def _write_last_run(ws: Any, job: Job, tracker: _Tracker, summary: dict[str, Any]) -> None:
    data = {
        "version": 1,
        "job_id": job.id,
        "summary": summary,
        "template": tracker.state.get("template"),
        "mockups": tracker.state.get("mockups"),
        "items": [_copy_item(item) for item in tracker.items],
    }
    path = ws.drafts / LAST_RUN_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.warning("could not save %s", path)


def _notify_end(ctx: AppContext, summary: dict[str, Any]) -> None:
    params = {"n": summary["created"], "total": summary["total"], "errors": summary["errors"],
              "checked": summary["checked"]}
    if summary["dry_run"]:
        ctx.notify(KIND, "notify.checked", params, tone="info", link="/tasarim-yukle")
    elif summary["stopped"]:
        params["reason"] = summary["stopped"]["code"]
        ctx.notify(KIND, "notify.stopped", params, tone="danger", link="/tasarim-yukle")
    elif summary["cancelled"]:
        ctx.notify(KIND, "notify.cancelled", params, tone="warning", link="/tasarim-yukle")
    elif summary["errors"]:
        ctx.notify(KIND, "notify.done_errors", params, tone="warning", link="/tasarim-yukle")
    else:
        ctx.notify(KIND, "notify.done", params, tone="success", link="/tasarim-yukle")


def start(req: Request) -> dict[str, Any]:
    ctx = _ctx(req)
    body = req.json_object()
    dry_run = body.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise ApiError(422, "invalid", "dry_run must be true or false", field="dry_run")
    with _start_lock:
        return _start(ctx, dry_run)


def _start(ctx: AppContext, dry_run: bool) -> dict[str, Any]:
    from ...drop import automation, catalog, stream
    from ...drop.template import Template

    if _active_job(ctx) is not None:
        raise ApiError(409, "busy", "A batch is already running.")
    info = _pending_info(ctx)
    blockers = [b for b in info["blockers"] if not (dry_run and b in _ETSY_BLOCKERS)]
    if blockers:
        raise ApiError(409, "setup_incomplete", "Finish the setup before starting.",
                       blockers=blockers)
    ws = ctx.workspace()
    template = Template.from_dict(ws.read_template())
    mockups_info = dict(info["mockups"])

    def work(job: Job) -> dict[str, Any]:
        tracker = _Tracker(job, dry_run=dry_run, template=info["template"],
                           mockups=mockups_info)
        try:
            if dry_run:
                try:
                    client = ctx.client(require_auth=False)  # research needs only the keys
                except ApiError:
                    client = None
            else:
                client = ctx.client()
            try:
                report = stream.run_stream(
                    ws, template, client, mockups=catalog.enabled_mockups(ws),
                    on_event=tracker.on_event, cancel=_JobCancel(job),
                    concurrency=CONCURRENCY, dry_run=dry_run,
                )
            except automation.UploadLocked as exc:
                raise ApiError(409, "locked", str(exc)) from exc
            except stream.TemplateGone as exc:
                raise ApiError(409, "template_gone", str(exc)) from exc
        except Exception as exc:
            from ..errors import describe

            tracker.abort()
            if not job.cancelled:
                ctx.notify(KIND, "notify.failed", {"code": describe(exc)["code"]},
                           tone="danger", link="/tasarim-yukle")
            raise
        summary = tracker.finish(report, ws)
        if report.items:
            _write_last_run(ws, job, tracker, summary)
            _notify_end(ctx, summary)
        if report.cancelled:
            job.check_cancel()  # ends the job as "cancelled"; the state keeps the results
        return summary

    job = ctx.jobs.start(
        KIND, "designs:job.title_dry" if dry_run else "designs:job.title", work,
        params={"n": info["count"], "dry_run": dry_run}, cancellable=True, refresh_status=True,
    )
    return job.summary()


def last_run(req: Request) -> dict[str, Any]:
    ctx = _ctx(req)
    path = ctx.workspace().drafts / LAST_RUN_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"run": None}
    return {"run": data if isinstance(data, dict) else None}


# --- recovery ---------------------------------------------------------------------------------


def forget(req: Request) -> dict[str, Any]:
    """Let a product the history holds as failed or uncertain be tried again.

    Only for entries that are not "ok": an "ok" entry is a real draft, and forgetting
    it would make a second one. The seller confirms they checked Etsy first.
    """
    from ...drop import automation

    ctx = _ctx(req)
    body = req.json_object()
    name = body.get("name")
    if not isinstance(name, str) or not name:
        raise ApiError(422, "invalid", "name is required", field="name")
    if body.get("confirm") is not True:
        raise ApiError(400, "confirm_required", "Forgetting an attempt needs {\"confirm\": true}.")
    if _active_job(ctx) is not None:
        raise ApiError(409, "busy", "Wait for the running batch to finish first.")
    shop = _shop_key(ctx)
    if shop is None:
        raise ApiError(409, "setup_needed", "Connect the shop first.", step="connect")
    ws = ctx.workspace()
    try:
        with automation.upload_lock(ws.root):
            path = automation.history_path(ws.root)
            state = automation.load_history(path)
            section = state.get(shop) or {}
            key = next((k for k in section if k.casefold() == name.casefold()), None)
            if key is None:
                raise ApiError(404, "not_found", "That product has no recorded attempt.")
            if section[key].get("status") == "ok":
                raise ApiError(409, "already_drafted", "That product already has a draft.")
            del section[key]
            state[shop] = section
            automation.save_history(path, state)
    except automation.UploadLocked as exc:
        raise ApiError(409, "locked", str(exc)) from exc
    ctx.set_status_soon()
    return {"ok": True, "name": key}


def unlock(req: Request) -> dict[str, Any]:
    """Remove the upload lock a crashed run left behind (the seller confirms)."""
    from ...drop import automation

    ctx = _ctx(req)
    body = req.json_object()
    if body.get("confirm") is not True:
        raise ApiError(400, "confirm_required", "Removing the lock needs {\"confirm\": true}.")
    if _active_job(ctx) is not None:
        raise ApiError(409, "busy", "A batch is running in this app right now.")
    path = automation.lock_path(ctx.workspace().root)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ApiError(500, "internal", f"Could not remove the lock: {exc}") from exc
    return {"ok": True}
