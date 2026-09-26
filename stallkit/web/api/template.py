"""Şablon İlan: the listing every new draft copies its business settings from.

Price, shipping profile, category, who/when made, processing time and return policy
are decisions about a business, not facts about a picture (see drop/template.py), so
the seller picks one listing they built by hand and stallkit copies it into
`product.json` in the products folder.

Endpoints:

    GET  /api/template
         -> {"template": <summary> | null, "problem": null | "malformed"}
    GET  /api/template/listings?refresh=0|1
         -> {"items": [<row>], "count": int, "truncated": bool, "currency": str | null,
             "fetched_at": epoch, "template_listing_id": int | null}
    GET  /api/template/preview/{listing_id}
         -> <summary> of that listing, nothing saved
    POST /api/template {"listing_id": int}
         -> {"template": <summary>}  (writes product.json)

    <row> = {listing_id, title, price, currency, thumb_url, state, num_favorers,
             product_type, has_variations}
    <summary> = {listing_id, title, state, thumb_url, currency, has_variations,
                 is_current, saved_at, ok_count, total, resolved,
                 fields: [{key, ok, required, value}]}

The seven `fields`, in display order, and their `value` shapes:

    price       {amount, currency}
    shipping    {id, title, origin_country, digital}
    category    {id, path: [names, root first]}
    who_made    {code}
    when_made   {code}
    processing  {readiness_state_id, readiness_state, min, max, label}
    returns     {id, accepts_returns, accepts_exchanges, deadline}

Names are resolved with the shop's shipping profiles, return policies and processing
profiles (cached in memory per Etsy client for a few minutes) and the seller taxonomy
(cached on disk for 7 days). A lookup Etsy refuses (a missing scope, say) leaves that
value unresolved (ids only) instead of failing the whole answer.
"""

from __future__ import annotations

import html
import json
import logging
import threading
import time
import weakref
from typing import TYPE_CHECKING, Any, Callable

from ...drop import cache as disk_cache
from ...drop import template as template_mod
from ...errors import EtsyApiError, StallKitError, ValidationError
from ..router import ApiError, Request

if TYPE_CHECKING:  # pragma: no cover
    from ..context import AppContext
    from ..router import Router

log = logging.getLogger("stallkit.web")

MAX_LISTINGS = 500
LISTINGS_TTL = 15 * 60
LOOKUP_TTL = 10 * 60
TAXONOMY_TTL = 7 * 24 * 3600
TAXONOMY_KEY = "seller-taxonomy-nodes-v1"
TAXONOMY_NAMESPACE = "taxonomy"
MAX_LISTING_ID = 10**12

FIELD_KEYS = (
    "price",
    "shipping",
    "category",
    "who_made",
    "when_made",
    "processing",
    "returns",
)


def register(r: Router, ctx: AppContext) -> None:
    api = TemplateApi(ctx)
    r.get("/api/template", api.current)
    r.get("/api/template/listings", api.listings)
    r.get("/api/template/preview/{listing_id:int}", api.preview)
    r.post("/api/template", api.save)


# --------------------------------------------------------------------------- helpers


def _listing_id(value: Any) -> int:
    """A listing id from a request, or 422 invalid."""
    if isinstance(value, bool):
        value = None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or not 0 < value < MAX_LISTING_ID:
        raise ApiError(422, "invalid", "listing_id must be a listing number", field="listing_id")
    return value


def _title(listing: dict[str, Any]) -> str:
    # Etsy sends titles with HTML entities (&amp;, &#39;); the page shows plain text.
    return html.unescape(str(listing.get("title") or "")).strip()


def _currency(listing: dict[str, Any]) -> str | None:
    price = listing.get("price")
    if isinstance(price, dict) and price.get("currency_code"):
        return str(price["currency_code"])
    return None


def _safe_image_url(url: Any) -> str | None:
    """Only https image URLs reach an <img src> (the page's CSP allows no others)."""
    if isinstance(url, str) and url.startswith("https://"):
        return url
    return None


def _thumb_url(listing: dict[str, Any]) -> str | None:
    images = [i for i in (listing.get("images") or []) if isinstance(i, dict)]
    if not images:
        return None
    first = min(images, key=lambda image: image.get("rank") or 0)
    for key in ("url_170x135", "url_570xN", "url_75x75", "url_fullxfull"):
        url = _safe_image_url(first.get(key))
        if url:
            return url
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _flatten_taxonomy(nodes: list[dict[str, Any]]) -> dict[int, list[str]]:
    """{taxonomy id: [root name, ..., leaf name]} for the whole seller taxonomy tree."""
    out: dict[int, list[str]] = {}
    stack: list[tuple[dict[str, Any], tuple[str, ...]]] = [
        (node, ()) for node in reversed(nodes or []) if isinstance(node, dict)
    ]
    while stack:
        node, trail = stack.pop()
        path = (*trail, str(node.get("name") or ""))
        node_id = _int(node.get("id"))
        if node_id is not None:
            out[node_id] = list(path)
        children = [c for c in (node.get("children") or []) if isinstance(c, dict)]
        stack.extend((child, path) for child in reversed(children))
    return out


def _lookup_refused(exc: EtsyApiError) -> bool:
    """A lookup failure that should leave names unresolved rather than fail the answer.

    Offline (0), a broken sign-in (401) and the rate limit (429) concern every call,
    so those still fail; a refusal of this one lookup (a missing scope, say) does not.
    """
    return exc.status not in (0, 401, 429)


class _ClientCache:
    """What one Etsy client (one shop, one sign-in) has told us recently."""

    def __init__(self) -> None:
        self.lock = threading.Lock()  # one listing fetch at a time
        self.items: list[dict[str, Any]] | None = None
        self.count = 0
        self.fetched_at = 0.0
        self.raw: dict[int, dict[str, Any]] = {}
        self.lookups: dict[str, tuple[float, dict[int, dict[str, Any]] | None]] = {}

    def listings_fresh(self) -> bool:
        return self.items is not None and time.time() - self.fetched_at < LISTINGS_TTL


# --------------------------------------------------------------------------- the API


class TemplateApi:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._lock = threading.Lock()
        self._caches: weakref.WeakKeyDictionary[Any, _ClientCache] = weakref.WeakKeyDictionary()
        self._taxonomy: dict[int, list[str]] | None = None
        self._taxonomy_at = 0.0

    # --- caches -----------------------------------------------------------------

    def _cache(self, client: Any) -> _ClientCache:
        # Keyed by the client object: the context makes a new one whenever the shop,
        # the keys or the sign-in change, so nothing leaks from one shop to another.
        with self._lock:
            found = self._caches.get(client)
            if found is None:
                found = self._caches[client] = _ClientCache()
            return found

    def _taxonomy_paths(self, client: Any | None) -> dict[int, list[str]]:
        """The flattened seller taxonomy: memory, else disk (7 days), else Etsy."""
        with self._lock:
            if self._taxonomy is not None and time.time() - self._taxonomy_at < TAXONOMY_TTL:
                return self._taxonomy
        stored = disk_cache.load(TAXONOMY_KEY, namespace=TAXONOMY_NAMESPACE, ttl=TAXONOMY_TTL)
        paths: dict[int, list[str]] | None = None
        if isinstance(stored, dict) and stored:
            paths = {}
            for key, value in stored.items():
                node_id = _int(key)
                if node_id is not None and isinstance(value, list):
                    paths[node_id] = [str(v) for v in value]
        if not paths:
            if client is None:
                return {}
            paths = _flatten_taxonomy(client.taxonomy_nodes())
            if paths:
                disk_cache.store(
                    TAXONOMY_KEY,
                    {str(k): v for k, v in paths.items()},
                    namespace=TAXONOMY_NAMESPACE,
                )
        with self._lock:
            self._taxonomy, self._taxonomy_at = paths, time.time()
        return paths

    def _lookup(
        self, cache: _ClientCache, name: str, fetch: Callable[[], list[dict[str, Any]]], id_key: str
    ) -> dict[int, dict[str, Any]] | None:
        """{id: record} for one of the shop's lists; None when Etsy refused it."""
        now = time.time()
        found = cache.lookups.get(name)
        if found is not None and now - found[0] < LOOKUP_TTL:
            return found[1]
        try:
            records = fetch()
        except EtsyApiError as exc:
            if not _lookup_refused(exc):
                raise
            log.warning("template: could not read %s (%s)", name, exc)
            cache.lookups[name] = (now, None)
            return None
        mapping: dict[int, dict[str, Any]] = {}
        for record in records or []:
            if isinstance(record, dict):
                record_id = _int(record.get(id_key))
                if record_id is not None:
                    mapping[record_id] = record
        cache.lookups[name] = (now, mapping)
        return mapping

    # --- Etsy reads -------------------------------------------------------------

    def _fetch_listings(self, client: Any, cache: _ClientCache, refresh: bool) -> None:
        with cache.lock:
            if cache.listings_fresh() and not refresh:
                return
            with client.attempts(3):
                items = list(
                    client.listings_by_shop("active", includes=["Images"], max_items=MAX_LISTINGS)
                )
                count = len(items)
                if count >= MAX_LISTINGS:
                    count = max(count, client.count_listings("active"))
            cache.items = [i for i in items if isinstance(i, dict) and _int(i.get("listing_id"))]
            cache.count = count
            cache.fetched_at = time.time()
            cache.raw = {int(i["listing_id"]): i for i in cache.items}

    def _fetch_listing(self, client: Any, listing_id: int) -> dict[str, Any]:
        """getListing, with the sign-in sent so a draft or inactive listing is readable.

        The OAS gives getListing API-key security only; the bearer token is sent as
        well because the seller's own non-active listings may need it (unverified).
        """
        with client.attempts(3):
            listing = client.get(
                f"/listings/{listing_id}",
                params={"includes": "Images"},
                authed=client.token is not None,
            )
            if not isinstance(listing, dict) or not listing.get("listing_id"):
                raise ApiError(404, "not_found", f"Etsy has no listing {listing_id}.")
            owner = _int(listing.get("shop_id"))
            if owner is not None and client.token is not None and owner != client.shop_id():
                raise ApiError(
                    422, "not_your_listing", "That listing belongs to another shop.",
                    listing_id=listing_id,
                )
        return listing

    # --- summaries --------------------------------------------------------------

    def _current_id(self) -> int | None:
        try:
            ws = self.ctx.workspace(create=False)
            data = json.loads(ws.template_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return _int(data.get("source_listing_id")) if isinstance(data, dict) else None

    def _shop_currency(self) -> str | None:
        return (self.ctx.status.get("shop") or {}).get("currency") or None

    def _summary(
        self,
        client: Any | None,
        cache: _ClientCache | None,
        fields: dict[str, Any],
        *,
        listing_id: int,
        title: str,
        currency: str | None,
        tolerant: bool,
        state: str | None = None,
        thumb_url: str | None = None,
        has_variations: bool = False,
    ) -> dict[str, Any]:
        """The seven rows for a set of template fields, names resolved where possible.

        tolerant: never raise for an Etsy problem (the saved template must always
        show); names then stay unresolved.
        """
        lookups: dict[str, dict[int, dict[str, Any]] | None] = {}
        paths: dict[int, list[str]] = {}
        resolved = True
        needed = [
            ("shipping", "shipping_profile_id", "shipping_profiles", "shipping_profile_id"),
            ("returns", "return_policy_id", "return_policies", "return_policy_id"),
            ("readiness", "readiness_state_id", "readiness_state_definitions", "readiness_state_id"),
        ]
        broken = client is None or cache is None
        for name, field_name, method, id_key in needed:
            if not fields.get(field_name):
                continue
            if broken:
                resolved = False
                continue
            try:
                with client.attempts(1 if tolerant else 3):
                    lookups[name] = self._lookup(cache, name, getattr(client, method), id_key)
            except (EtsyApiError, StallKitError, ApiError) as exc:
                if not tolerant:
                    raise
                log.warning("template: names unresolved (%s)", exc)
                broken, resolved = True, False
                continue
            if lookups[name] is None:
                resolved = False
        if fields.get("taxonomy_id"):
            try:
                if broken:
                    paths = self._taxonomy_paths(None)  # the disk copy, if there is one
                else:
                    with client.attempts(1 if tolerant else 3):
                        paths = self._taxonomy_paths(client)
            except (EtsyApiError, StallKitError, ApiError) as exc:
                if not tolerant and not (isinstance(exc, EtsyApiError) and _lookup_refused(exc)):
                    raise
                log.warning("template: taxonomy unavailable (%s)", exc)
                paths = {}
            if _int(fields.get("taxonomy_id")) not in paths:
                resolved = False

        rows = _rows(fields, currency=currency, lookups=lookups, paths=paths)
        ok_count = sum(1 for row in rows if row["ok"])
        current = self._current_id()
        return {
            "listing_id": listing_id,
            "title": title,
            "state": state,
            "thumb_url": thumb_url,
            "currency": currency,
            "has_variations": bool(has_variations),
            "is_current": current == listing_id,
            "saved_at": None,
            "ok_count": ok_count,
            "total": len(rows),
            "resolved": resolved,
            "fields": rows,
        }

    def _listing_summary(
        self, client: Any, cache: _ClientCache, listing: dict[str, Any]
    ) -> dict[str, Any]:
        captured = template_mod.capture(listing)
        return self._summary(
            client,
            cache,
            captured.fields,
            listing_id=captured.source_listing_id,
            title=_title(listing),
            currency=_currency(listing) or self._shop_currency(),
            tolerant=False,
            state=listing.get("state") if isinstance(listing.get("state"), str) else None,
            thumb_url=_thumb_url(listing),
            has_variations=bool(listing.get("has_variations")),
        )

    # --- handlers ---------------------------------------------------------------

    def current(self, req: Request) -> dict[str, Any]:
        ws = self.ctx.workspace(create=False)
        path = ws.template_path
        if not path.is_file():
            return {"template": None, "problem": None}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            captured = template_mod.Template.from_dict(data)
            saved_at = path.stat().st_mtime
        except (OSError, ValueError, ValidationError) as exc:
            log.warning("template: product.json unreadable (%s)", exc)
            return {"template": None, "problem": "malformed"}

        client = cache = None
        try:
            client = self.ctx.client()
            cache = self._cache(client)
        except ApiError:
            client = cache = None
        raw = cache.raw.get(captured.source_listing_id) if cache is not None else None
        summary = self._summary(
            client,
            cache,
            captured.fields,
            listing_id=captured.source_listing_id,
            title=html.unescape(captured.source_title).strip(),
            currency=str(data.get("currency_code") or "") or self._shop_currency(),
            tolerant=True,
            state=raw.get("state") if raw else None,
            thumb_url=_thumb_url(raw) if raw else None,
            has_variations=bool(data.get("has_variations")),
        )
        summary["is_current"] = True
        summary["saved_at"] = round(saved_at, 3)
        return {"template": summary, "problem": None}

    def listings(self, req: Request) -> dict[str, Any]:
        refresh = req.bool_query("refresh", False)
        client = self.ctx.client()
        cache = self._cache(client)
        self._fetch_listings(client, cache, refresh)
        try:
            with client.attempts(3):
                paths = self._taxonomy_paths(client)
        except EtsyApiError as exc:
            if not _lookup_refused(exc):
                raise
            log.warning("template: taxonomy unavailable (%s)", exc)
            paths = {}
        items = []
        for listing in cache.items or []:
            price = listing.get("price")
            taxonomy = paths.get(_int(listing.get("taxonomy_id")) or 0) or []
            items.append({
                "listing_id": int(listing["listing_id"]),
                "title": _title(listing),
                "price": template_mod.money(price),
                "currency": _currency(listing),
                "thumb_url": _thumb_url(listing),
                "state": listing.get("state") or "active",
                "num_favorers": _int(listing.get("num_favorers")) or 0,
                "product_type": taxonomy[-1] if taxonomy else None,
                "has_variations": bool(listing.get("has_variations")),
            })
        return {
            "items": items,
            "count": max(cache.count, len(items)),
            "truncated": cache.count > len(items),
            "currency": self._shop_currency(),
            "fetched_at": round(cache.fetched_at, 3),
            "template_listing_id": self._current_id(),
        }

    def preview(self, req: Request) -> dict[str, Any]:
        listing_id = _listing_id(req.params.get("listing_id"))
        client = self.ctx.client()
        cache = self._cache(client)
        listing = cache.raw.get(listing_id) if cache.listings_fresh() else None
        if listing is None:
            listing = self._fetch_listing(client, listing_id)
            cache.raw[listing_id] = listing
        return self._listing_summary(client, cache, listing)

    def save(self, req: Request) -> dict[str, Any]:
        listing_id = _listing_id(req.json_object().get("listing_id"))
        client = self.ctx.client()
        cache = self._cache(client)
        # Always the listing as it is now, not the cached copy: the price may have moved.
        listing = self._fetch_listing(client, listing_id)
        captured = template_mod.capture(listing)
        data = captured.to_dict()
        data["source_title"] = _title(listing)
        # Two facts product.json did not hold before, for this page (Template ignores them).
        data["currency_code"] = _currency(listing) or self._shop_currency()
        data["has_variations"] = bool(listing.get("has_variations"))
        ws = self.ctx.workspace()
        ws.write_template(data)
        self.ctx.update_shop_prefs(template_listing=str(listing_id))
        cache.raw[listing_id] = listing
        self.ctx.set_status_soon(0.0)
        summary = self._listing_summary(client, cache, listing)
        summary["is_current"] = True
        try:
            summary["saved_at"] = round(ws.template_path.stat().st_mtime, 3)
        except OSError:
            summary["saved_at"] = round(time.time(), 3)
        return {"template": summary}


# --------------------------------------------------------------------------- the rows


def _row(key: str, ok: bool, value: dict[str, Any], *, required: bool) -> dict[str, Any]:
    return {"key": key, "ok": bool(ok), "required": required, "value": value}


def _rows(
    fields: dict[str, Any],
    *,
    currency: str | None,
    lookups: dict[str, dict[int, dict[str, Any]] | None],
    paths: dict[int, list[str]],
) -> list[dict[str, Any]]:
    rows = []

    price = fields.get("price")
    amount = float(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None
    rows.append(_row("price", bool(amount and amount > 0), {"amount": amount, "currency": currency},
                     required=True))

    listing_type = fields.get("type") or "physical"
    digital = listing_type == "download"
    profile_id = _int(fields.get("shipping_profile_id"))
    profile = (lookups.get("shipping") or {}).get(profile_id or 0) or {}
    rows.append(_row(
        "shipping",
        digital or profile_id is not None,
        {
            "id": profile_id,
            "title": (html.unescape(str(profile.get("title"))).strip() or None)
            if profile.get("title") else None,
            "origin_country": profile.get("origin_country_iso") or None,
            "digital": digital,
        },
        required=not digital,
    ))

    taxonomy_id = _int(fields.get("taxonomy_id"))
    rows.append(_row(
        "category",
        taxonomy_id is not None,
        {"id": taxonomy_id, "path": paths.get(taxonomy_id or 0) or []},
        required=True,
    ))

    who = fields.get("who_made") or None
    rows.append(_row("who_made", who is not None, {"code": who}, required=True))
    when = fields.get("when_made") or None
    rows.append(_row("when_made", when is not None, {"code": when}, required=True))

    readiness_id = _int(fields.get("readiness_state_id"))
    readiness = (lookups.get("readiness") or {}).get(readiness_id or 0) or {}
    low = _int(readiness.get("min_processing_days"))
    high = _int(readiness.get("max_processing_days"))
    if low is None and high is None:
        low, high = _int(fields.get("processing_min")), _int(fields.get("processing_max"))
    rows.append(_row(
        "processing",
        readiness_id is not None or low is not None or high is not None,
        {
            "readiness_state_id": readiness_id,
            "readiness_state": readiness.get("readiness_state") or None,
            "min": low,
            "max": high,
            "label": readiness.get("processing_days_display_label") or None,
        },
        required=False,
    ))

    policy_id = _int(fields.get("return_policy_id"))
    policy = (lookups.get("returns") or {}).get(policy_id or 0)
    rows.append(_row(
        "returns",
        policy_id is not None,
        {
            "id": policy_id,
            "accepts_returns": bool(policy.get("accepts_returns")) if policy else None,
            "accepts_exchanges": bool(policy.get("accepts_exchanges")) if policy else None,
            "deadline": _int(policy.get("return_deadline")) if policy else None,
        },
        required=False,
    ))
    return rows
