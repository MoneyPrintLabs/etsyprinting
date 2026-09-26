"""SEO endpoints: the audit list, tag research and the one-listing fix.

    GET  /api/seo/audit?state=active|draft&refresh=0|1
    GET  /api/seo/research?keyword=&listing_id=&refresh=0|1
    POST /api/seo/fix/{listing_id}   {"tags": [...], "title"?: str, "materials"?: [...], "confirm": true}
    GET  /api/seo/export.csv?state=active|draft

The scoring itself is `stallkit.seo.audit_listing`; this module only adds what a
screen needs on top of it: numbers for every issue (the UI words them by code, in
Turkish or English, instead of showing the library's English message), a safe,
automatic fix proposal per listing, and a short-lived cache so switching tabs does
not re-read the whole shop from Etsy.
"""

from __future__ import annotations

import csv
import io
import re
import threading
import time
import weakref
from collections import Counter
from typing import TYPE_CHECKING, Any

from ... import seo
from ...config import MAX_MATERIAL_LEN, MAX_MATERIALS, MAX_TAG_LEN, MAX_TAGS, MAX_TITLE_LEN
from ...drop import cache as research_cache
from ...errors import EtsyApiError
from ...listings import bad_tag_chars, build_payload, validate_tags
from ..router import ApiError, Request, Response

if TYPE_CHECKING:  # pragma: no cover
    from ..context import AppContext
    from ..router import Router

STATES = ("active", "draft")
AUDIT_TTL = 300.0  # seconds an audit is reused unless the page asks for a rescan
AUDIT_MAX = 2000  # listings read per state (20 requests of 100)
# A page waits for these reads: give up after three tries (a few seconds), not five.
ATTEMPTS = 3
READY_SCORE = 85  # "Hazır": this score or better, and nothing worse than an info
CONCEPT_WORDS = 3  # the research keyword a listing suggests: its first telling words

# Market research: how many ranking listings to sample, how many rows to send back.
# 300 listings = three requests of 100 (findAllListingsActive allows limit <= 100).
RESEARCH_SAMPLE = 300
RESEARCH_ROWS = 15
RESEARCH_NAMESPACE = "research"
KEYWORD_MAX = 80

# Words that say little about what the item is; skipped when picking a listing's
# research keyword ("Handmade Ceramic Coffee Mug, Gift" -> "ceramic coffee mug").
GENERIC_WORDS = {
    "handmade", "custom", "personalized", "personalised", "gift", "gifts", "unique",
    "cute", "funny", "best", "new", "sale", "idea", "ideas", "present", "perfect",
}

# Same word pattern as stallkit.seo, but capturing, for splitting a title into words
# and the text between them.
_WORD_SPLIT = re.compile(r"([^\W\d_]+(?:'[^\W\d_]+)?|\d+)", re.UNICODE)
_SEP = r"[,;:|/&+\-–—·]"
# Etsy's title rule (updateListing): "You can only use the %, :, & and + characters once each."
_TITLE_ONCE = "%:&+"

CSV_COLUMNS = ["listing_id", "score", "grade", "title", "url", "issue_count", "issues"]


def register(r: Router, ctx: AppContext) -> None:
    r.get("/api/seo/audit", audit)
    r.get("/api/seo/research", research)
    r.post("/api/seo/fix/{id:int}", fix)
    r.get("/api/seo/export.csv", export_csv)


# --- the per-app audit cache -------------------------------------------------------------


class _Store:
    """Listings read for the audit, per (shop, state), for AUDIT_TTL seconds."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.entries: dict[tuple[str, str], dict[str, Any]] = {}
        self.fetch_locks: dict[tuple[str, str], threading.Lock] = {}

    def fetch_lock(self, key: tuple[str, str]) -> threading.Lock:
        with self.lock:
            return self.fetch_locks.setdefault(key, threading.Lock())


_stores: weakref.WeakKeyDictionary[Any, _Store] = weakref.WeakKeyDictionary()
_stores_lock = threading.Lock()


def _store(ctx: AppContext) -> _Store:
    with _stores_lock:
        store = _stores.get(ctx)
        if store is None:
            store = _stores[ctx] = _Store()
        return store


def _state(req: Request) -> str:
    state = (req.query.get("state") or "active").strip().lower()
    if state not in STATES:
        raise ApiError(422, "invalid", "state must be active or draft", field="state")
    return state


def _load(ctx: AppContext, state: str, *, refresh: bool) -> dict[str, Any]:
    """The cached read of one state's listings: {listings, counts, at, truncated, cached}."""
    client = ctx.client()  # 409 setup_needed before anything else
    key = (ctx.shop_id, state)
    store = _store(ctx)
    with store.fetch_lock(key):
        with store.lock:
            entry = store.entries.get(key)
        fresh = (
            entry is not None
            and entry["client"] == id(client)
            and time.time() - entry["at"] < AUDIT_TTL
        )
        if fresh and not refresh:
            return {**entry, "cached": True}
        with client.attempts(ATTEMPTS):
            listings = list(
                client.listings_by_shop(state, includes=["Images"], max_items=AUDIT_MAX)
            )
            truncated = len(listings) >= AUDIT_MAX
            counts: dict[str, int | None] = {state: len(listings)}
            for name in STATES:
                if name == state and not truncated:
                    continue
                try:
                    counts[name] = client.count_listings(name)
                except EtsyApiError:
                    counts[name] = None
        entry = {
            "listings": listings,
            "counts": counts,
            "at": round(time.time(), 3),
            "truncated": truncated,
            "client": id(client),
        }
        with store.lock:
            store.entries[key] = entry
        return {**entry, "cached": False}


def _cached_listing(ctx: AppContext, listing_id: int) -> dict[str, Any] | None:
    store = _store(ctx)
    with store.lock:
        entries = [e for (shop, _state), e in store.entries.items() if shop == ctx.shop_id]
    for entry in entries:
        for listing in entry["listings"]:
            if _listing_id(listing) == listing_id:
                return listing
    return None


def _replace_cached(ctx: AppContext, listing: dict[str, Any]) -> None:
    listing_id = _listing_id(listing)
    store = _store(ctx)
    with store.lock:
        for (shop, _state), entry in store.entries.items():
            if shop != ctx.shop_id:
                continue
            entry["listings"] = [
                listing if _listing_id(old) == listing_id else old for old in entry["listings"]
            ]


def forget(ctx: AppContext) -> None:
    """Drop the cached audit of the open shop, e.g. after a listing was edited elsewhere.

    Another area may call this; the next GET /api/seo/audit then reads Etsy again.
    """
    store = _store(ctx)
    with store.lock:
        for key in [k for k in store.entries if k[0] == ctx.shop_id]:
            del store.entries[key]


def _listing_id(listing: dict[str, Any]) -> int:
    try:
        return int(listing.get("listing_id") or 0)
    except (TypeError, ValueError):
        return 0


def _content(listing: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """What a fix changes, to tell whether a listing changed since it was audited."""
    return (
        " ".join(str(listing.get("title") or "").split()),
        [str(t) for t in (listing.get("tags") or [])],
        [str(m) for m in (listing.get("materials") or [])],
    )


# --- issues with numbers ------------------------------------------------------------------


def _tags(listing: dict[str, Any]) -> list[str]:
    """The listing's tags as audit_listing counts them (empty ones dropped)."""
    return [str(t) for t in (listing.get("tags") or []) if t]


def issue_params(code: str, listing: dict[str, Any]) -> dict[str, Any]:
    """The numbers behind one issue, recomputed from the listing (the library's Issue
    only carries an English sentence). Keys are what the page's strings interpolate."""
    title = (listing.get("title") or "").strip()
    description = (listing.get("description") or "").strip()
    tags = _tags(listing)
    lowered = [t.lower().strip() for t in tags]
    if code == "title.too_long":
        return {"title_len": len(title), "max": MAX_TITLE_LEN}
    if code == "title.too_short":
        return {"title_len": len(title), "min": seo.TITLE_MIN_USEFUL}
    if code == "title.front_empty":
        return {"visible": seo.TITLE_VISIBLE_CHARS}
    if code == "title.repetition":
        words = repeated_title_words(title)
        return {"words": words, "word": words[0] if words else "", "count": len(words)}
    if code == "title.comma_spam":
        return {"commas": title.count(",")}
    if code == "title.caps":
        shouty = [w for w in re.findall(r"\b[A-ZÇĞİÖŞÜ]{4,}\b", title) if w.isupper()]
        return {"words": shouty[:3], "word": shouty[0] if shouty else ""}
    if code in ("tags.missing", "tags.too_many", "tags.unused"):
        return {"tags_used": len(tags), "max": MAX_TAGS, "free_slots": max(0, MAX_TAGS - len(tags))}
    if code == "tags.too_long":
        long_tags = [t for t in tags if len(t) > MAX_TAG_LEN]
        return {"tags": long_tags, "tag": long_tags[0] if long_tags else "", "count": len(long_tags),
                "max": MAX_TAG_LEN}
    if code == "tags.duplicate":
        dupes = sorted({t for t in lowered if lowered.count(t) > 1})
        return {"tags": dupes, "tag": dupes[0] if dupes else "", "count": len(dupes)}
    if code == "tags.near_duplicate":
        pairs = seo._near_duplicates(lowered)
        first = pairs[0] if pairs else ("", "")
        return {"pairs": [list(p) for p in pairs], "a": first[0], "b": first[1], "count": len(pairs)}
    if code == "tags.single_word":
        single = sum(1 for t in tags if len(t.split()) == 1)
        return {"single": single, "tags_used": len(tags)}
    if code == "tags.title_mismatch":
        title_words = set(seo.content_words(title))
        orphans = [t for t in tags if not (set(seo.content_words(t)) & title_words)]
        return {"orphans": len(orphans), "tags_used": len(tags)}
    if code == "description.thin":
        return {"description_len": len(description), "min": seo.DESCRIPTION_MIN_USEFUL}
    return {}


def repeated_title_words(title: str) -> list[str]:
    """Content words the audit calls stuffing (three or more times), alphabetical."""
    counts = Counter(seo.content_words(title))
    return sorted(w for w, c in counts.items() if c >= 3)


def _sorted_issues(issues: list[seo.Issue]) -> list[seo.Issue]:
    """Heaviest first; the library's own order (title, tags, description, ...) within."""
    order = {issue.code: n for n, issue in enumerate(issues)}
    return sorted(issues, key=lambda i: (-seo.SEVERITY_WEIGHT.get(i.severity, 5), order[i.code]))


def shop_issue_params(code: str, shop: seo.ShopAudit) -> dict[str, Any]:
    if code == "shop.tag_overlap":
        cutoff = max(2, int(shop.listings * seo.SHOP_WIDE_SHARE))
        return {
            "tags": len(shop.shop_wide),
            "cutoff": cutoff,
            "listings": shop.listings,
            "top": [{"tag": tag, "count": count} for tag, count in shop.shop_wide[:8]],
        }
    if code == "shop.too_alike":
        return {"crowded": len(shop.crowded), "min": seo.MIN_DISTINCTIVE_TAGS,
                "listings": shop.listings}
    return {}


# --- the fix proposal ---------------------------------------------------------------------


def _near(a: str, b: str) -> bool:
    """The audit's near-duplicate rule (seo._near_duplicates), for two lowered tags."""
    return a != b and (a.rstrip("s") == b.rstrip("s") or a.replace(" ", "") == b.replace(" ", ""))


def tag_proposal(tags: list[str]) -> dict[str, Any]:
    """Which tags to keep and which to drop, automatically and safely.

    Exact and near duplicates lose to the first of their kind; tags Etsy would refuse
    (over 20 characters, disallowed characters) are dropped; anything past 13 too.
    Nothing is reworded.
    """
    keep: list[str] = []
    remove: list[dict[str, Any]] = []
    first_of: dict[str, str] = {}
    for raw in tags:
        tag = " ".join(str(raw).split())
        if not tag:
            continue
        low = tag.lower()
        if len(tag) > MAX_TAG_LEN:
            remove.append({"tag": tag, "reason": "too_long"})
        elif bad_tag_chars(tag):
            remove.append({"tag": tag, "reason": "bad_chars"})
        elif low in first_of:
            remove.append({"tag": tag, "reason": "duplicate", "of": first_of[low]})
        elif any(_near(k.lower(), low) for k in keep):
            of = next(k for k in keep if _near(k.lower(), low))
            remove.append({"tag": tag, "reason": "near_duplicate", "of": of})
        elif len(keep) >= MAX_TAGS:
            remove.append({"tag": tag, "reason": "too_many"})
        else:
            keep.append(tag)
            first_of[low] = tag
    return {"keep": keep, "remove": remove, "free_slots": MAX_TAGS - len(keep)}


def dedupe_title(title: str, words: list[str] | None = None) -> str:
    """The title with each stuffed word kept only where it first appears.

    "Floral Print Poster, Floral Wall Art, Floral Print" -> "Floral Print Poster, Wall Art, Print".
    Separators left dangling by a removed word are tidied up.
    """
    stuffed = set(words if words is not None else repeated_title_words(title))
    if not stuffed:
        return title
    parts = _WORD_SPLIT.split(title)
    seen: set[str] = set()
    out: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:  # a word (the split keeps captured words at odd indexes)
            low = part.lower()
            if low in stuffed and low in seen:
                continue
            seen.add(low)
        out.append(part)
    text = "".join(out)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?)])", r"\1", text)
    text = re.sub(rf"({_SEP})(?:\s*{_SEP})+", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"^[\s,.;:|/&+\-–—·]+|[\s,;:|/&+\-–—·]+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def proposal(listing: dict[str, Any], codes: set[str]) -> dict[str, Any]:
    """What the "Düzelt" dialog offers for one listing."""
    title = (listing.get("title") or "").strip()
    tags = tag_proposal(_tags(listing))
    title_fix = None
    if "title.repetition" in codes:
        words = repeated_title_words(title)
        after = dedupe_title(title, words)
        if after and after != title and len(after) <= MAX_TITLE_LEN:
            title_fix = {"before": title, "after": after, "words": words}
    manual = [
        code for code in (
            "title.missing", "title.too_short", "title.too_long", "title.front_empty",
            "title.caps", "title.comma_spam", "description.missing", "description.thin",
            "description.opening",
        ) if code in codes
    ]
    return {
        **tags,
        "title": title_fix,
        "materials": not (listing.get("materials") or []),
        "manual": manual,
    }


# --- one audited listing, as the page gets it ---------------------------------------------------


def concept(title: str) -> str:
    """A research keyword for a listing: its first few telling words."""
    words = seo.content_words(title)
    telling = [w for w in words if w not in GENERIC_WORDS and not w.isdigit()]
    return " ".join((telling or words)[:CONCEPT_WORDS])


def _thumb(listing: dict[str, Any]) -> str | None:
    images = [i for i in (listing.get("images") or []) if isinstance(i, dict)]
    if not images:
        return None
    first = sorted(images, key=lambda i: i.get("rank") or 0)[0]
    return first.get("url_170x135") or first.get("url_75x75") or first.get("url_570xN")


def audit_item(listing: dict[str, Any]) -> dict[str, Any]:
    result = seo.audit_listing(listing)
    issues = _sorted_issues(result.issues)
    codes = {i.code for i in issues}
    serious = any(i.severity in ("error", "warn") for i in issues)
    return {
        "listing_id": result.listing_id,
        "title": result.title,
        "state": listing.get("state") or "",
        "url": result.url,
        "score": result.score,
        "grade": result.grade,
        "ready": result.score >= READY_SCORE and not serious,
        "issues": [
            {"code": i.code, "severity": i.severity, "params": issue_params(i.code, listing)}
            for i in issues
        ],
        "tags": _tags(listing),
        "materials": [str(m) for m in (listing.get("materials") or []) if m],
        "concept": concept(result.title),
        "thumb": _thumb(listing),
        "fix": proposal(listing, codes),
    }


# --- endpoints ----------------------------------------------------------------------------


def audit(req: Request) -> dict[str, Any]:
    ctx = req.ctx
    assert ctx is not None
    state = _state(req)
    data = _load(ctx, state, refresh=req.bool_query("refresh"))
    listings = data["listings"]
    items = [audit_item(listing) for listing in listings]
    items.sort(key=lambda item: (item["score"], item["title"].lower(), item["listing_id"]))
    shop = seo.audit_shop(listings)
    scanned = len(items)
    return {
        "state": state,
        "items": items,
        "scanned": scanned,
        "needs_fix": sum(1 for item in items if not item["ready"]),
        "average": round(sum(i["score"] for i in items) / scanned) if scanned else None,
        "counts": data["counts"],
        "shop_issues": [
            {"code": i.code, "severity": i.severity, "params": shop_issue_params(i.code, shop)}
            for i in shop.issues
        ],
        "checked_at": data["at"],
        "cached": data["cached"],
        "truncated": data["truncated"],
    }


def _keyword(req: Request) -> str:
    keyword = " ".join((req.query.get("keyword") or "").split()).lower()
    if not keyword:
        raise ApiError(422, "invalid", "keyword is empty", field="keyword")
    return keyword[:KEYWORD_MAX]


def market(client: Any, keyword: str, *, refresh: bool = False) -> tuple[seo.MarketReport, bool]:
    """seo.research for `keyword`, through the drop pipeline's 7-day research cache.

    Same key and value shape as drop/pipeline.py (`"<concept>|<sample>"`, the
    report's fields), so either side can reuse what the other fetched.
    """
    key = f"{keyword}|{RESEARCH_SAMPLE}"
    if not refresh:
        cached = research_cache.load(key, namespace=RESEARCH_NAMESPACE)
        if isinstance(cached, dict):
            try:
                return seo.MarketReport(**cached), True
            except TypeError:
                pass  # an older or foreign shape: fetch again
    with client.attempts(ATTEMPTS):
        report = seo.research(client, keyword, sample=RESEARCH_SAMPLE)
    research_cache.store(key, report.__dict__, namespace=RESEARCH_NAMESPACE)
    return report, False


def research(req: Request) -> dict[str, Any]:
    ctx = req.ctx
    assert ctx is not None
    keyword = _keyword(req)
    listing_id = req.int_query("listing_id", None, min=1)
    client = ctx.client(require_auth=False)  # a public search: the API key is enough
    existing: list[str] | None = None
    if listing_id is not None:
        listing = _cached_listing(ctx, listing_id)
        if listing is None and client.token is not None:
            try:
                with client.attempts(ATTEMPTS):
                    found = client.listings_batch([listing_id])
            except EtsyApiError:
                found = []
            listing = found[0] if found else None
        if listing is not None:
            existing = _tags(listing)
    report, cached = market(client, keyword, refresh=req.bool_query("refresh"))
    have = {t.lower().strip() for t in (existing or [])}
    rows = []
    for tag, count in report.tags:
        tag = str(tag)
        if tag in have or len(tag) > MAX_TAG_LEN or bad_tag_chars(tag):
            continue
        share = count / report.sampled if report.sampled else 0.0
        rows.append({"tag": tag, "count": int(count), "share": round(share, 4)})
        if len(rows) >= RESEARCH_ROWS:
            break
    suggestions = seo.suggest_tags(report, existing=existing or [], extra=RESEARCH_ROWS)
    allowed = {row["tag"] for row in rows}
    return {
        "keyword": keyword,
        "sample": RESEARCH_SAMPLE,
        "sampled": report.sampled,
        "listing_id": listing_id if existing is not None else None,
        "tags": rows,
        "add_now": [t for t in suggestions.add_now if t in allowed],
        "needs_a_swap": [t for t in suggestions.needs_a_swap if t in allowed],
        "free_slots": suggestions.free_slots,
        "used_slots": suggestions.used_slots,
        "cached": cached,
    }


def _clean_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ApiError(422, "invalid", f"{field} must be a list of strings", field=field)
    return [" ".join(v.split()) for v in value if v.strip()]


def fix(req: Request) -> dict[str, Any]:
    """Write a reviewed fix to the live listing: its tags, and optionally title and materials.

    The page shows the proposal and a confirmation first; the request must say
    {"confirm": true}. Everything is checked locally before one PATCH goes out.
    """
    ctx = req.ctx
    assert ctx is not None
    listing_id = req.params["id"]
    body = req.json_object()
    if body.get("confirm") is not True:
        raise ApiError(400, "confirm_required", 'Updating a live listing needs {"confirm": true}.')

    tags = _clean_list(body.get("tags"), "tags")
    if not tags:
        raise ApiError(422, "invalid", "at least one tag is needed", field="tags")
    problems = validate_tags(tags)
    if problems:
        raise ApiError(422, "invalid", "; ".join(problems), field="tags")
    row = {"tags": "|".join(tags)}

    title = body.get("title")
    if title is not None:
        if not isinstance(title, str) or not title.strip():
            raise ApiError(422, "invalid", "title must be text", field="title")
        title = " ".join(title.split())
        for ch in _TITLE_ONCE:
            if title.count(ch) > 1:
                raise ApiError(422, "invalid", f"the title may use {ch} only once", field="title")
        row["title"] = title

    materials = _clean_list(body.get("materials"), "materials")
    if materials:
        if len(materials) > MAX_MATERIALS:
            raise ApiError(422, "invalid", f"at most {MAX_MATERIALS} materials", field="materials")
        for material in materials:
            # updateListing: materials contain only letters, numbers and whitespace.
            if len(material) > MAX_MATERIAL_LEN or not all(ch.isalnum() or ch.isspace() for ch in material):
                raise ApiError(
                    422, "invalid",
                    f"material {material!r}: letters, numbers and spaces only, "
                    f"at most {MAX_MATERIAL_LEN} characters",
                    field="materials",
                )
        row["materials"] = "|".join(materials)

    payload = build_payload(row, is_update=True)  # ValidationError -> 422 invalid

    before = _cached_listing(ctx, listing_id)
    client = ctx.client()
    if before is not None:
        # The page proposed this fix from the audit (up to 5 minutes old). If the listing
        # changed on Etsy since, sending the whole tag list would undo that edit: refuse,
        # and let the page rescan. A failed read skips the check; the PATCH will say.
        try:
            with client.attempts(ATTEMPTS):
                found = client.listings_batch([listing_id])
        except EtsyApiError:
            found = []
        current = found[0] if found else None
        if current is not None and _content(current) != _content(before):
            _replace_cached(ctx, {**before, **current})
            raise ApiError(
                409, "seo_stale",
                "The listing changed on Etsy after the audit. Rescan and review the fix again.",
            )
    updated = client.update_listing(listing_id, payload)
    listing = {**(before or {}), **(updated if isinstance(updated, dict) else {})}
    listing.setdefault("listing_id", listing_id)
    _replace_cached(ctx, listing)
    item = audit_item(listing)
    return {
        "item": item,
        "before": seo.audit_listing(before).score if before else None,
        "sent": sorted(payload),
    }


def export_csv(req: Request) -> Response:
    """The audit as CSV, the same columns as `stallkit seo audit --out`."""
    ctx = req.ctx
    assert ctx is not None
    state = _state(req)
    data = _load(ctx, state, refresh=req.bool_query("refresh"))
    audits = sorted((seo.audit_listing(listing) for listing in data["listings"]),
                    key=lambda a: a.score)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for item in audits:
        writer.writerow({
            "listing_id": item.listing_id,
            "score": item.score,
            "grade": item.grade,
            "title": item.title,
            "url": item.url,
            "issue_count": len(item.issues),
            "issues": item.summary(),
        })
    # UTF-8 with a BOM, like every CSV stallkit writes: Excel needs it for Turkish letters.
    body = ("﻿" + buffer.getvalue()).encode("utf-8")
    name = f"seo-{state}-{time.strftime('%Y-%m-%d')}.csv"
    return Response.bytes(
        body, "text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
