"""İlanlar (the listings table) and the listing detail page.

    GET   /api/listings                 one page of a tab, with counts, filters and ids
    GET   /api/listings/{id}            one listing: images, SEO audit, category, source file
    PATCH /api/listings/{id}            edit title / tags / description
    POST  /api/listings/{id}/publish    publish one draft
    POST  /api/listings/publish         publish many drafts (job "publish")
    GET   /api/listings/export.csv      a tab's listings in the CSV shape `listings push` reads
    GET   /api/listings/template.csv    the same columns, no rows
    POST  /api/listings/import          check a CSV (raw body); nothing is sent to Etsy
    POST  /api/listings/import/apply    send a checked CSV to Etsy (job "listings-import")

Every write to a live listing needs `"confirm": true` in its body: the page asks the
seller first, and the server refuses a write that skipped the question.

Listings are read with getListingsByShop per state (includes=Images) and kept for two
minutes per shop and state. The cache is dropped early after any write made here, and
whenever a job that creates or edits listings (Tasarım Yükle, SEO fixes) has finished.
"""

from __future__ import annotations

import csv
import json
import math
import re
import secrets
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ... import csvio, seo
from ... import listings as listings_mod
from ...client import walk_taxonomy
from ...config import MAX_LISTING_IMAGES, MAX_TAG_LEN, MAX_TAGS, MAX_TITLE_LEN
from ...errors import EtsyApiError, ValidationError
from .. import files
from ..router import ApiError, Request, Response

if TYPE_CHECKING:  # pragma: no cover
    from ..context import AppContext
    from ..router import Router

# getListingsByShop state enum (OAS): active inactive sold_out draft removed expired.
# "removed" listings are gone from the shop, so "Hepsi" is the sum of the other five.
STATES = ("draft", "active", "inactive", "sold_out", "expired")
TABS: dict[str, tuple[str, ...]] = {"draft": ("draft",), "active": ("active",), "all": STATES}
SORTS = ("updated", "seo", "title", "price")
SEO_BANDS = ("high", "mid", "low")
CACHE_TTL = 120.0
# A shop with more listings than this in one state is shown in part (flagged `truncated`).
MAX_PER_STATE = 3000
MAX_NAV_IDS = 1000
PER_PAGE_DEFAULT = 8
READ_ATTEMPTS = 2
# Jobs whose end means the shop's listings changed.
# Jobs whose end makes the cached listings stale (the kinds their modules start).
LISTING_JOB_KINDS = frozenset({"designs", "publish", "listings-import"})
EDIT_URL = "https://www.etsy.com/your/shops/me/listing-editor/edit/{id}"
TAXONOMY_TTL = 30 * 24 * 3600
IMPORT_TTL = 30 * 60
MAX_CSV_BYTES = 5 * 1024 * 1024
# ShopListing (OAS) has url and num_favorers but no view count, so there is no "views".
EXPORT_COLUMNS = listings_mod.LISTING_COLUMNS + ["url", "num_favorers"]

_lock = threading.Lock()
_listing_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]], bool]] = {}
_count_cache: dict[tuple[Any, ...], tuple[float, int]] = {}
_fetch_locks: dict[tuple[Any, ...], threading.Lock] = {}
_taxonomy: dict[str, Any] = {"map": None, "failed_at": 0.0}
_imports: dict[str, dict[str, Any]] = {}
_alpha_cache: dict[tuple[str, float], bool | None] = {}


def register(r: Router, ctx: AppContext) -> None:
    # Another area wrote listings (an SEO fix, new drafts): drop this shop's cache.
    ctx.on_change("listings", lambda c: invalidate(c), name="listings")
    r.get("/api/listings", list_listings)
    r.get("/api/listings/export.csv", export_csv)
    r.get("/api/listings/template.csv", template_csv)
    r.post("/api/listings/import", import_check)
    r.post("/api/listings/import/apply", import_apply)
    r.post("/api/listings/publish", publish_many)
    r.get("/api/listings/{id:int}", one_listing)
    r.patch("/api/listings/{id:int}", edit_listing)
    r.post("/api/listings/{id:int}/publish", publish_one)


def _ctx(req: Request) -> AppContext:
    assert req.ctx is not None
    return req.ctx


# --- the listing cache ----------------------------------------------------------------


def _key(ctx: AppContext, client: Any, state: str) -> tuple[Any, ...]:
    return (ctx.shop_id, client.shop_id(), state)


def _changed_since(ctx: AppContext, since: float) -> bool:
    """True when a job that writes listings has finished after `since`."""
    for job in ctx.jobs.list():
        if job.kind in LISTING_JOB_KINDS and job.finished_at and job.finished_at >= since:
            return True
    return False


def _fresh(ctx: AppContext, stored_at: float) -> bool:
    return time.time() - stored_at < CACHE_TTL and not _changed_since(ctx, stored_at)


def invalidate(ctx: AppContext | None = None, states: tuple[str, ...] | None = None) -> None:
    """Forget cached listings (of one shop and some states, or everything)."""
    with _lock:
        for cache in (_listing_cache, _count_cache):
            for key in list(cache):
                if ctx is not None and key[0] != ctx.shop_id:
                    continue
                if states is not None and key[2] not in states:
                    continue
                del cache[key]


def _state_listings(
    ctx: AppContext, client: Any, state: str, *, force: bool = False
) -> tuple[list[dict[str, Any]], bool]:
    """Every listing of the shop in `state` (with images), from the cache when fresh."""
    key = _key(ctx, client, state)
    with _lock:
        fetch_lock = _fetch_locks.setdefault(key, threading.Lock())
    with fetch_lock:  # two tabs asking at once make one set of requests
        with _lock:
            hit = _listing_cache.get(key)
        if hit is not None and not force and _fresh(ctx, hit[0]):
            return hit[1], hit[2]
        started = time.time()
        rows = list(client.listings_by_shop(state, includes=["Images"], max_items=MAX_PER_STATE + 1))
        truncated = len(rows) > MAX_PER_STATE
        rows = rows[:MAX_PER_STATE]
        with _lock:
            _listing_cache[key] = (started, rows, truncated)
            _count_cache.pop(key, None)
        return rows, truncated


def _cached_listings(ctx: AppContext, client: Any, state: str) -> list[dict[str, Any]] | None:
    key = _key(ctx, client, state)
    with _lock:
        hit = _listing_cache.get(key)
    if hit is not None and _fresh(ctx, hit[0]):
        return hit[1]
    return None


def _state_count(ctx: AppContext, client: Any, state: str) -> int | None:
    """How many listings are in `state`: from loaded listings, a cached count, or Etsy."""
    rows = _cached_listings(ctx, client, state)
    if rows is not None:
        return len(rows)
    key = _key(ctx, client, state)
    with _lock:
        hit = _count_cache.get(key)
    if hit is not None and _fresh(ctx, hit[0]):
        return hit[1]
    started = time.time()
    try:
        count = client.count_listings(state)
    except EtsyApiError as exc:
        if exc.status in (400, 404):  # a state this shop cannot be asked about
            return None
        raise
    with _lock:
        _count_cache[key] = (started, count)
    return count


def _remember(ctx: AppContext, client: Any, listing: dict[str, Any]) -> None:
    """Put an edited listing back into the cache, so the table shows the change at once."""
    try:
        listing_id = int(listing.get("listing_id") or 0)
    except (TypeError, ValueError):
        return
    state = str(listing.get("state") or "")
    moved = {state}
    with _lock:
        for key, (stored_at, rows, truncated) in list(_listing_cache.items()):
            if key[0] != ctx.shop_id:
                continue
            kept = [row for row in rows if _id(row) != listing_id]
            old = next((row for row in rows if _id(row) == listing_id), None)
            if old is not None:
                moved.add(str(old.get("state") or key[2]))
            if key[2] == state:
                merged = dict(old or {})
                merged.update(listing)
                if "images" not in listing and old is not None:
                    merged["images"] = old.get("images") or []
                kept.insert(0, merged)
            if old is not None or key[2] == state:
                _listing_cache[key] = (stored_at, kept, truncated)
        # Counts change only when the listing changed state (or was not known before).
        if len(moved) > 1:
            for key in [k for k in _count_cache if k[0] == ctx.shop_id and k[2] in moved]:
                del _count_cache[key]


def _id(listing: dict[str, Any]) -> int:
    try:
        return int(listing.get("listing_id") or 0)
    except (TypeError, ValueError):
        return 0


# --- what a row shows ----------------------------------------------------------------------


def _taxonomy_paths(client: Any) -> dict[int, str]:
    """taxonomy id -> "A > B > C", cached on disk for a month (one key-only request)."""
    from ...drop import cache

    with _lock:
        known = _taxonomy["map"]
        failed_at = _taxonomy["failed_at"]
    if known is not None:
        return known
    if time.time() - failed_at < 300:
        return {}
    stored = cache.load("seller-taxonomy-v1", namespace="taxonomy", ttl=TAXONOMY_TTL)
    paths: dict[int, str] = {}
    if isinstance(stored, dict) and stored:
        paths = {int(k): str(v) for k, v in stored.items() if str(k).isdigit()}
    if not paths:
        try:
            with client.attempts(2):
                nodes = client.taxonomy_nodes()
        except EtsyApiError:
            with _lock:
                _taxonomy["failed_at"] = time.time()
            return {}
        paths = {node_id: path for node_id, path in walk_taxonomy(nodes) if node_id}
        if paths:
            cache.store("seller-taxonomy-v1", {str(k): v for k, v in paths.items()},
                        namespace="taxonomy")
    with _lock:
        _taxonomy["map"] = paths or None
        if not paths:
            _taxonomy["failed_at"] = time.time()
    return paths


def _guess_type(text: str) -> str:
    """A mockup type (tshirt, mug, poster, ...) from a name like "T-shirts" or a title."""
    from ...drop import catalog

    words = re.findall(r"[^\W\d_]+", text or "")
    if not words:
        return "other"
    singular = [w[:-1] for w in words if len(w) > 3 and w.lower().endswith("s")]
    kind, _colour = catalog.guess(" ".join(words + singular))
    return kind


def _product_type(listing: dict[str, Any], paths: dict[int, str]) -> tuple[str, str]:
    """(type key, the taxonomy leaf name) of a listing."""
    try:
        path = paths.get(int(listing.get("taxonomy_id") or 0), "")
    except (TypeError, ValueError):
        path = ""
    leaf = path.split(" > ")[-1] if path else ""
    for text in (leaf, path, str(listing.get("title") or "")):
        kind = _guess_type(text)
        if kind != "other":
            return kind, leaf
    return "other", leaf


def _source_names(ctx: AppContext, client: Any) -> dict[int, str]:
    """listing id -> the design file (or folder) stallkit made it from (upload-history.json)."""
    try:
        data = json.loads((ctx.workspace_root() / "upload-history.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    own = data.get(str(client.shop_id()))
    sections = [own] if isinstance(own, dict) else [s for s in data.values() if isinstance(s, dict)]
    out: dict[int, str] = {}
    for section in sections:
        for name, entry in section.items():
            if not isinstance(entry, dict):
                continue
            try:
                listing_id = int(entry.get("listing_id") or 0)
            except (TypeError, ValueError):
                continue
            if listing_id:
                out[listing_id] = str(name)
    return out


def _money(price: Any) -> tuple[float | None, str | None]:
    if not isinstance(price, dict):
        return None, None
    amount, divisor = price.get("amount"), price.get("divisor") or 100
    if not isinstance(amount, (int, float)) or not isinstance(divisor, (int, float)) or not divisor:
        return None, price.get("currency_code")
    return round(amount / divisor, 2), price.get("currency_code")


def _images(listing: dict[str, Any]) -> list[dict[str, Any]]:
    images = listing.get("images") or listing.get("Images") or []
    images = [i for i in images if isinstance(i, dict)]
    return sorted(images, key=lambda image: image.get("rank") or 0)


def _updated(listing: dict[str, Any]) -> int | None:
    for field in ("updated_timestamp", "last_modified_timestamp", "state_timestamp",
                  "created_timestamp"):
        value = listing.get(field)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return None


def _row(listing: dict[str, Any], paths: dict[int, str], sources: dict[int, str]) -> dict[str, Any]:
    listing_id = _id(listing)
    audit = seo.audit_listing(listing)
    kind, type_name = _product_type(listing, paths)
    price, currency = _money(listing.get("price"))
    images = _images(listing)
    first = images[0] if images else {}
    tags = [t for t in (listing.get("tags") or []) if t]
    return {
        "id": listing_id,
        "title": str(listing.get("title") or ""),
        "state": str(listing.get("state") or ""),
        "tags": len(tags),
        "seo": audit.score,
        "issues": len(audit.issues),
        "price": price,
        "currency": currency,
        "updated": _updated(listing),
        "thumb": first.get("url_170x135") or first.get("url_570xN") or first.get("url_75x75"),
        "images": len(images),
        "type": kind,
        "type_name": type_name,
        "source": sources.get(listing_id),
        "url": str(listing.get("url") or ""),
        "_tags": tags,
    }


_FOLD = str.maketrans({"ı": "i", "ş": "s", "ç": "c", "ğ": "g", "ö": "o", "ü": "u"})


def _fold(text: str) -> str:
    text = str(text or "").replace("İ", "i").replace("I", "i").lower().translate(_FOLD)
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _seo_band(score: int) -> str:
    return "high" if score >= 80 else "mid" if score >= 60 else "low"


def _sort(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    by_updated = sorted(rows, key=lambda r: (r["updated"] or 0, r["id"]), reverse=True)
    if sort == "seo":
        return sorted(by_updated, key=lambda r: r["seo"])
    if sort == "title":
        return sorted(by_updated, key=lambda r: _fold(r["title"]))
    if sort == "price":
        return sorted(by_updated, key=lambda r: (r["price"] is None, r["price"] or 0))
    return by_updated


# --- GET /api/listings ------------------------------------------------------------------------


def list_listings(req: Request) -> dict[str, Any]:
    ctx = _ctx(req)
    tab = req.query.get("tab") or req.query.get("state") or "draft"
    if tab not in TABS:
        raise ApiError(422, "invalid", "tab must be draft, active or all", field="tab")
    sort = req.query.get("sort") or "updated"
    if sort not in SORTS:
        raise ApiError(422, "invalid", f"sort must be one of {', '.join(SORTS)}", field="sort")
    band = req.query.get("seo") or ""
    if band and band not in SEO_BANDS:
        raise ApiError(422, "invalid", "seo must be high, mid or low", field="seo")
    kind = req.query.get("type") or ""
    per_page = req.int_query("per_page", PER_PAGE_DEFAULT, min=1, max=100) or PER_PAGE_DEFAULT
    page = req.int_query("page", 1, min=1, max=100000) or 1
    force = req.bool_query("refresh")
    query = _fold(req.query.get("q", "")).strip()

    client = ctx.client()
    if force:
        invalidate(ctx)
    listings: list[dict[str, Any]] = []
    truncated = False
    # Someone is waiting on this page: a failing Etsy gets two tries, not five.
    with client.attempts(READ_ATTEMPTS):
        for state in TABS[tab]:
            rows, cut = _state_listings(ctx, client, state)
            listings.extend(rows)
            truncated = truncated or cut
        counts: dict[str, int | None] = {
            state: _state_count(ctx, client, state) for state in STATES
        }
    counts["all"] = sum(n for n in counts.values() if n)

    paths = _taxonomy_paths(client)
    sources = _source_names(ctx, client)
    rows = [_row(listing, paths, sources) for listing in listings]
    if query:
        rows = [
            r for r in rows
            if query in _fold(r["title"])
            or any(query in _fold(tag) for tag in r["_tags"])
            or (r["source"] and query in _fold(r["source"]))
            or query == str(r["id"])
        ]
    type_counts: dict[str, int] = {}
    for r in rows:
        type_counts[r["type"]] = type_counts.get(r["type"], 0) + 1
    if kind:
        rows = [r for r in rows if r["type"] == kind]
    if band:
        rows = [r for r in rows if _seo_band(r["seo"]) == band]
    rows = _sort(rows, sort)

    total = len(rows)
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    start = (page - 1) * per_page
    items = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[start:start + per_page]]
    currency = next((r["currency"] for r in rows if r["currency"]), None) or (
        ctx.status.get("shop") or {}
    ).get("currency")
    return {
        "tab": tab,
        "items": items,
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "start": start + 1 if total else 0,
        "end": min(start + per_page, total),
        "counts": counts,
        "types": [
            {"id": key, "count": n}
            for key, n in sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "ids": [r["id"] for r in rows[:MAX_NAV_IDS]],
        "currency": currency,
        "truncated": truncated,
    }


# --- GET /api/listings/{id} ------------------------------------------------------------------


def _find_listing(ctx: AppContext, client: Any, listing_id: int) -> dict[str, Any]:
    """The seller's own listing, in any state. 404 when the shop has no such listing."""
    for state in STATES:
        rows = _cached_listings(ctx, client, state)
        found = next((r for r in rows or [] if _id(r) == listing_id), None)
        if found is not None:
            return found
    # getListingsByListingIds is one request, but Etsy may leave drafts out of it.
    try:
        batch = client.listings_batch([listing_id], includes=["Images"])
    except EtsyApiError as exc:
        if exc.status not in (400, 403, 404):
            raise
        batch = []
    shop_id = client.shop_id()
    for listing in batch:
        if _id(listing) == listing_id and listing.get("shop_id") in (None, shop_id):
            return listing
    for state in STATES:
        rows, _cut = _state_listings(ctx, client, state)
        found = next((r for r in rows if _id(r) == listing_id), None)
        if found is not None:
            return found
    raise ApiError(404, "not_found", f"Your shop has no listing {listing_id}.")


def _is_transparent(path: Path) -> bool | None:
    """True when a PNG really uses its alpha channel. None when it cannot be read."""
    from PIL import Image

    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime)
    if key in _alpha_cache:
        return _alpha_cache[key]
    result: bool | None
    try:
        with Image.open(path) as image:
            if image.mode in ("RGBA", "LA", "PA") or (
                image.mode == "P" and "transparency" in image.info
            ):
                image.thumbnail((256, 256))
                alpha = image.convert("RGBA").getchannel("A")
                result = alpha.getextrema()[0] < 255
            else:
                result = False
    except (OSError, ValueError, Image.DecompressionBombError):
        result = None
    if len(_alpha_cache) > 500:
        _alpha_cache.clear()
    _alpha_cache[key] = result
    return result


def _source_info(ctx: AppContext, name: str | None) -> dict[str, Any] | None:
    """Where the design behind a listing is, for the "Kaynak tasarım" row."""
    from ...drop.workspace import ARCHIVE_DIR, IMAGE_SUFFIXES, PRODUCTS_DIR

    if not name:
        return None
    info: dict[str, Any] = {"name": name, "exists": False, "kind": "file", "rel": None,
                            "mtime": None, "format": None, "transparent": None, "files": 0}
    root = ctx.workspace_root()
    safe = Path(name).name
    if not safe or safe != name:
        return info
    for folder in (PRODUCTS_DIR, ARCHIVE_DIR):
        path = root / folder / safe
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            info.update(exists=True, rel=f"{folder}/{safe}", mtime=int(path.stat().st_mtime),
                        format=path.suffix.lower().lstrip(".").replace("jpeg", "jpg"), files=1)
            if path.suffix.lower() == ".png":
                info["transparent"] = _is_transparent(path)
            return info
        if path.is_dir():
            images = sorted(
                (p for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
                key=lambda p: p.name.casefold(),
            )
            info.update(exists=True, kind="folder", files=len(images))
            if images:
                info.update(rel=f"{folder}/{safe}/{images[0].name}",
                            mtime=int(images[0].stat().st_mtime))
            return info
    if Path(safe).suffix.lower() not in IMAGE_SUFFIXES:
        info["kind"] = "folder"
    return info


def _image_view(image: dict[str, Any], listing_kind: str) -> dict[str, Any]:
    from ...drop import catalog

    alt = str(image.get("alt_text") or "")
    kind, colour = catalog.guess(re.sub(r"[.]", " ", alt)) if alt else ("other", "")
    return {
        "id": image.get("listing_image_id"),
        "rank": image.get("rank"),
        "url": image.get("url_570xN") or image.get("url_fullxfull") or image.get("url_170x135"),
        "full": image.get("url_fullxfull"),
        "thumb": image.get("url_170x135") or image.get("url_75x75") or image.get("url_570xN"),
        "width": image.get("full_width"),
        "height": image.get("full_height"),
        "alt": alt,
        "type": kind if kind != "other" else listing_kind,
        "color": colour,
        "color_key": _fold(colour),
    }


def _detail(ctx: AppContext, client: Any, listing: dict[str, Any],
            images: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    paths = _taxonomy_paths(client)
    sources = _source_names(ctx, client)
    row = _row(listing, paths, sources)
    audit = seo.audit_listing(listing)
    try:
        path = paths.get(int(listing.get("taxonomy_id") or 0), "")
    except (TypeError, ValueError):
        path = ""
    state = row["state"]
    listing_id = row["id"]
    etsy_url = row["url"] if state == "active" and row["url"] else EDIT_URL.format(id=listing_id)
    shown = images if images is not None else _images(listing)
    return {
        "listing": {
            "id": listing_id,
            "title": row["title"],
            "description": str(listing.get("description") or ""),
            "state": state,
            "tags": row["_tags"],
            "materials": [m for m in (listing.get("materials") or []) if m],
            "price": row["price"],
            "currency": row["currency"],
            "quantity": listing.get("quantity"),
            "url": row["url"],
            "taxonomy_id": listing.get("taxonomy_id"),
            "updated": row["updated"],
            "type": row["type"],
            "type_name": row["type_name"],
        },
        "images": [_image_view(image, row["type"]) for image in shown],
        "audit": {
            "score": audit.score,
            "grade": audit.grade,
            "issues": [
                {"code": i.code, "severity": i.severity, "message": i.message} for i in audit.issues
            ],
        },
        "category": {"path": path, "short": " › ".join(path.split(" > ")[-2:]) if path else ""},
        "source": _source_info(ctx, row["source"]),
        "limits": {"title": MAX_TITLE_LEN, "tags": MAX_TAGS, "tag_len": MAX_TAG_LEN,
                   "images": MAX_LISTING_IMAGES},
        "etsy_url": etsy_url,
    }


def one_listing(req: Request) -> dict[str, Any]:
    ctx = _ctx(req)
    listing_id = req.params["id"]
    client = ctx.client()
    with client.attempts(READ_ATTEMPTS):
        listing = _find_listing(ctx, client, listing_id)
        try:
            images = client.listing_images(listing_id)
        except EtsyApiError as exc:
            if exc.status not in (400, 403, 404):
                raise
            images = _images(listing)
    return _detail(ctx, client, listing, images or _images(listing))


# --- PATCH /api/listings/{id} ------------------------------------------------------------------

_TITLE_ONCE = "%:&+"
_TITLE_SYMBOLS = set("™©®")


def title_problems(title: str) -> list[str]:
    """Etsy's title rules (OAS updateListing.title): length, characters, %:&+ once each."""
    problems = []
    if len(title) > MAX_TITLE_LEN:
        problems.append(f"title is {len(title)} chars, max {MAX_TITLE_LEN}")
    bad = sorted({
        ch for ch in title
        if not (unicodedata.category(ch)[0] in "LP"
                or unicodedata.category(ch) in ("Nd", "Sm", "Zs")
                or ch in _TITLE_SYMBOLS)
    })
    if bad:
        problems.append(f"title contains characters Etsy refuses: {''.join(bad)}")
    for ch in _TITLE_ONCE:
        if title.count(ch) > 1:
            problems.append(f"title may use {ch!r} only once")
    return problems


def _clean_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        raise ApiError(422, "invalid", "tags must be a list of strings", field="tags")
    tags = [" ".join(t.split()) for t in raw]
    tags = [t for t in tags if t]
    if not tags:
        raise ApiError(422, "tags_empty", "Keep at least one tag.", field="tags")
    problems = listings_mod.validate_tags(tags)
    if problems:
        raise ApiError(422, "invalid", "; ".join(problems), field="tags")
    return tags


def edit_listing(req: Request) -> dict[str, Any]:
    ctx = _ctx(req)
    listing_id = req.params["id"]
    body = req.json_object()
    row: dict[str, str] = {}
    if "title" in body:
        if not isinstance(body["title"], str) or not body["title"].strip():
            raise ApiError(422, "title_empty", "The title cannot be empty.", field="title")
        title = " ".join(body["title"].split())
        problems = title_problems(title)
        if problems:
            raise ApiError(422, "invalid", "; ".join(problems), field="title")
        row["title"] = title
    if "tags" in body:
        row["tags"] = csvio.MULTI_SEP.join(_clean_tags(body["tags"]))
    if "description" in body:
        if not isinstance(body["description"], str) or not body["description"].strip():
            raise ApiError(422, "description_empty", "The description cannot be empty.",
                           field="description")
        row["description"] = body["description"].strip()
    if not row:
        raise ApiError(422, "invalid", "Send title, tags or description.")
    try:
        payload = listings_mod.build_payload(row, is_update=True)
    except ValidationError as exc:
        raise ApiError(422, "invalid", str(exc)) from exc

    client = ctx.client()
    listing = _find_listing(ctx, client, listing_id)
    if listing.get("state") == "active" and body.get("confirm") is not True:
        raise ApiError(409, "confirm_required", "This listing is live; send confirm: true.")
    updated = client.update_listing(listing_id, payload)
    merged = dict(listing)
    merged.update(payload)
    if isinstance(updated, dict):
        merged.update({k: v for k, v in updated.items() if v is not None})
    merged["images"] = _images(listing)
    _remember(ctx, client, merged)
    ctx.changed("listings", source="listings")  # the SEO audit, the dashboard, ...
    return _detail(ctx, client, merged)


# --- publishing ------------------------------------------------------------------------------------


def _etsy_reason(exc: EtsyApiError) -> str:
    """Etsy's own words about a refused write (English; Etsy sends nothing else)."""
    return (exc.message or f"HTTP {exc.status}").strip()


def _fatal(exc: EtsyApiError) -> bool:
    """Errors that would repeat for every listing: stop instead of trying the rest."""
    return exc.status in (0, 401, 429) or (exc.status == 403 and "api key" in (
        f"{exc.message} {exc.body}".lower()))


def publish_one(req: Request) -> dict[str, Any]:
    ctx = _ctx(req)
    listing_id = req.params["id"]
    body = req.json_object()
    if body.get("confirm") is not True:
        raise ApiError(409, "confirm_required", "Publishing needs confirm: true.")
    client = ctx.client()
    listing = _find_listing(ctx, client, listing_id)
    if listing.get("state") == "active":
        raise ApiError(409, "already_active", "This listing is already live.")
    try:
        updated = client.update_listing(listing_id, {"state": "active"})
    except EtsyApiError as exc:
        if _fatal(exc) or exc.status == 404:
            raise
        raise ApiError(422, "publish_refused", exc.message, reason=_etsy_reason(exc),
                       hint=exc.hint(), status=exc.status) from exc
    invalidate(ctx, ("draft", "active", "inactive", "expired", "sold_out"))
    ctx.changed("listings", source="listings")
    merged = dict(listing)
    merged["state"] = "active"
    if isinstance(updated, dict):
        merged.update({k: v for k, v in updated.items() if v is not None})
    merged["images"] = _images(listing)
    return _detail(ctx, client, merged)


def publish_many(req: Request) -> dict[str, Any]:
    ctx = _ctx(req)
    body = req.json_object()
    raw = body.get("ids")
    if not isinstance(raw, list) or not raw:
        raise ApiError(422, "invalid", "ids must be a non-empty list of listing ids", field="ids")
    if not all(isinstance(i, int) and not isinstance(i, bool) and i > 0 for i in raw):
        raise ApiError(422, "invalid", "ids must be listing ids (whole numbers)", field="ids")
    ids = list(dict.fromkeys(raw))
    if len(ids) > 500:
        raise ApiError(422, "invalid", "Publish at most 500 listings at once.", field="ids")
    if body.get("confirm") is not True:
        raise ApiError(409, "confirm_required", "Publishing needs confirm: true.")
    client = ctx.client()  # 409 setup_needed now rather than inside the job
    known: dict[int, str] = {}  # what the table showed, to skip listings already live
    try:
        with client.attempts(1):
            for state in STATES:
                for listing in _cached_listings(ctx, client, state) or []:
                    known[_id(listing)] = str(listing.get("state") or state)
    except EtsyApiError:
        known = {}  # Etsy unreachable: the job reports it

    def work(job: Any) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        ok = failed = skipped = 0
        job.set_state(total=len(ids), results=results)
        try:
            for n, listing_id in enumerate(ids, 1):
                job.check_cancel()
                item: dict[str, Any] = {"id": listing_id, "status": "ok", "message": ""}
                if known.get(listing_id) == "active":
                    item.update(status="skipped", message="already active")
                    skipped += 1
                else:
                    try:
                        ctx.client().update_listing(listing_id, {"state": "active"})
                        ok += 1
                    except EtsyApiError as exc:
                        if _fatal(exc):
                            raise
                        item.update(status="error", message=_etsy_reason(exc),
                                    hint=exc.hint(), code=exc.status)
                        failed += 1
                results.append(item)
                job.emit("row", **item)
                job.set_state(results=results, ok=ok, failed=failed, skipped=skipped)
                job.progress(n, len(ids), label=str(listing_id))
        finally:
            invalidate(ctx, STATES)
            ctx.changed("listings", source="listings")
        tone = "success" if not failed else "warning" if ok else "danger"
        key = "notify.published" if not failed else "notify.publish_partial"
        ctx.notify("listings", key, {"n": ok, "ok": ok, "failed": failed}, tone=tone,
                   link="/ilanlar?tab=active" if ok else "/ilanlar")
        return {"published": ok, "failed": failed, "skipped": skipped, "results": results}

    job = ctx.jobs.start("publish", "listings:job.publish", work,
                         params={"n": len(ids)}, cancellable=True)
    return job.summary()


# --- CSV ------------------------------------------------------------------------------------------


class _CachedSource:
    """Looks like a client to listings.pull(), but reads the cached listings."""

    def __init__(self, ctx: AppContext, client: Any) -> None:
        self.ctx, self.client = ctx, client

    def listings_by_shop(self, state: str = "active", **_kw: Any) -> Any:
        rows, _cut = _state_listings(self.ctx, self.client, state)
        return iter(rows)


def _csv_bytes(rows: list[dict[str, Any]], columns: list[str]) -> bytes:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "listings.csv"
        csvio.write_rows(path, rows, columns=columns)
        return path.read_bytes()


def _download(data: bytes, filename: str) -> Response:
    return Response.bytes(data, "text/csv; charset=utf-8", {
        "Content-Disposition": f'attachment; filename="{filename}"',
    })


def export_csv(req: Request) -> Response:
    ctx = _ctx(req)
    tab = req.query.get("tab") or req.query.get("state") or "draft"
    if tab not in TABS and tab not in STATES:
        raise ApiError(422, "invalid", "tab must be draft, active, all or a listing state",
                       field="tab")
    client = ctx.client()
    source = _CachedSource(ctx, client)
    rows: list[dict[str, Any]] = []
    for state in TABS.get(tab, (tab,)):
        rows.extend(listings_mod.pull(source, state=state))  # type: ignore[arg-type]
    stamp = time.strftime("%Y-%m-%d")
    return _download(_csv_bytes(rows, EXPORT_COLUMNS), f"stallkit-listings-{tab}-{stamp}.csv")


def template_csv(req: Request) -> Response:
    return _download(_csv_bytes([], listings_mod.LISTING_COLUMNS), "stallkit-listings-template.csv")


def _read_csv(data: bytes) -> list[dict[str, str]]:
    if not data.strip():
        raise ApiError(422, "csv_empty", "The file is empty.")
    if len(data) > MAX_CSV_BYTES:
        raise ApiError(413, "too_large", "A listings CSV may be at most 5 MB.")
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ApiError(422, "csv_encoding", "Save the file as CSV UTF-8 and try again.") from exc
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "import.csv"
        path.write_bytes(data)
        try:
            rows = csvio.read_rows(path)
        except (ValidationError, csv.Error) as exc:
            raise ApiError(422, "invalid", str(exc)) from exc
    if not rows:
        raise ApiError(422, "csv_empty", "The file has a header but no rows.")
    if "listing_id" not in rows[0] and "title" not in rows[0]:
        raise ApiError(422, "csv_columns", "This is not a listings CSV (no listing_id or title).")
    return rows


def _result(result: Any) -> dict[str, Any]:
    return {
        "row": result.row,
        "action": result.action,
        "status": result.status,
        "listing_id": result.listing_id,
        "title": result.title,
        "message": result.message,
        "warnings": list(result.warnings),
    }


def _prune_imports() -> None:
    now = time.time()
    for token in [t for t, v in _imports.items() if now - v["at"] > IMPORT_TTL]:
        del _imports[token]
    while len(_imports) > 5:
        del _imports[min(_imports, key=lambda t: _imports[t]["at"])]


def workspace_images(root: Path) -> listings_mod.ImageResolver:
    """The `images` column of an uploaded CSV, confined to the workspace folder.

    An uploaded CSV has no folder of its own and may come from anyone (a shared
    "template"), so its image paths are not trusted the way the CLI trusts a CSV the
    seller keeps next to their pictures. Each value must be a relative path inside the
    workspace (for example `2-PRODUCTS/sunset.png`). An absolute path, a drive, a `~`,
    a UNC `//server/share` path or a `..` is refused from the text alone, before
    anything on disk (or on the network) is looked at, and a link that leads out of
    the folder is refused too.
    """

    def refuse(value: str) -> ValidationError:
        return ValidationError(
            f"image {value!r}: images must be inside the stallkit folder, "
            "written like 2-PRODUCTS/design.png"
        )

    def resolve(value: str) -> Path:
        text = value.strip()
        parts = text.replace("\\", "/").split("/")
        if (
            not text
            or text.startswith(("/", "\\", "~"))  # absolute, UNC (\\server), home
            or ":" in text  # a drive (C:\, C:x) or an alternate data stream
            or "\x00" in text
            or ".." in parts
        ):
            raise refuse(value)
        target = files.resolve_inside(root, text)
        if target is None:
            raise refuse(value)
        return target

    return resolve


def import_check(req: Request) -> dict[str, Any]:
    """Validate a CSV the way `listings push --dry-run` does. Nothing reaches Etsy."""
    ctx = _ctx(req)
    rows = _read_csv(req.body)
    base = ctx.workspace_root()
    report = listings_mod.push(None, rows, base_dir=base, dry_run=True,
                               image_resolver=workspace_images(base))
    results = [_result(r) for r in report.results]
    creates = sum(1 for r in results if r["action"] == "create" and r["status"] != "error")
    updates = sum(1 for r in results if r["action"] == "update" and r["status"] != "error")
    publishes = sum(
        1 for row, r in zip(rows, results)
        if r["action"] == "update" and (row.get("state") or "").strip().lower() == "active"
    )
    token = secrets.token_urlsafe(12)
    with _lock:
        _prune_imports()
        _imports[token] = {"rows": rows, "at": time.time(), "shop": ctx.shop_id, "base": str(base)}
    return {
        "token": token,
        "rows": len(rows),
        "creates": creates,
        "updates": updates,
        "publishes": publishes,
        "errors": report.errors,
        "warnings": sum(1 for r in results if r["warnings"]),
        "results": results,
    }


def import_apply(req: Request) -> dict[str, Any]:
    ctx = _ctx(req)
    body = req.json_object()
    token = body.get("token")
    if body.get("confirm") is not True:
        raise ApiError(409, "confirm_required", "Sending a CSV needs confirm: true.")
    with _lock:
        _prune_imports()
        pending = _imports.get(token) if isinstance(token, str) else None
    if pending is None or pending["shop"] != ctx.shop_id:
        raise ApiError(410, "import_expired", "Check the file again before sending it.")
    rows, base = pending["rows"], Path(pending["base"])
    resolver = workspace_images(base)
    check = listings_mod.push(None, rows, base_dir=base, dry_run=True, image_resolver=resolver)
    if check.errors:
        raise ApiError(422, "import_invalid", "Some rows are invalid; nothing was sent.",
                       n=check.errors)
    ctx.client()  # 409 setup_needed now rather than inside the job
    with _lock:
        _imports.pop(token, None)

    def work(job: Any) -> dict[str, Any]:
        results: list[dict[str, Any]] = []

        def progress(result: Any) -> None:
            item = _result(result)
            results.append(item)
            job.emit("row", **item)
            job.set_state(results=results)
            job.progress(len(results), len(rows), label=result.title)
            if job.cancelled:  # between rows: the one just sent is finished
                job.check_cancel()

        try:
            report = listings_mod.push(ctx.client(), rows, base_dir=base, on_progress=progress,
                                       image_resolver=resolver)
        finally:
            invalidate(ctx, STATES)
            ctx.changed("listings", source="listings")
        summary = {"created": report.created, "updated": report.updated,
                   "partial": report.partial, "errors": report.errors,
                   "aborted": report.aborted, "reason": report.aborted_reason}
        tone = "success" if not report.errors and not report.partial else "warning"
        ctx.notify("listings", "notify.imported", summary, tone=tone, link="/ilanlar?tab=all")
        return {**summary, "results": results}

    job = ctx.jobs.start("listings-import", "listings:job.import", work,
                         params={"n": len(rows)}, cancellable=True)
    return job.summary()
