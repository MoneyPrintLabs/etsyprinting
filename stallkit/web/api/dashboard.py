"""Panel (dashboard) endpoints.

    GET /api/dashboard        setup steps, recent activity, the last stats (no Etsy call)
    GET /api/dashboard/stats  the six numbers, read from Etsy (cached; ?refresh=1 re-reads)

The stats are read in parallel through the shared client (its rate limiter still
holds Etsy's QPS) and each one may fail on its own: a stat that cannot be read comes
back with `value: null` and an error code, the others are still shown.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from ..errors import describe
from ..router import ApiError, Request

if TYPE_CHECKING:  # pragma: no cover
    from ..context import AppContext
    from ..router import Router

COUNT_TTL = 60.0  # listing and order counts
SAMPLE_TTL = 300.0  # the SEO sample and the month's revenue
SEO_SAMPLE = 100  # "a cheap sample": the first page of active listings
SEO_LOW = 60  # a listing under this score needs attention (the score ring turns red)
REVENUE_MAX_RECEIPTS = 1000
RECENT_LIMIT = 8
STAT_NAMES = ("active", "draft", "to_ship", "seo", "revenue", "quota")
# A dashboard read must stay quick: two tries per request, not the batch default.
ATTEMPTS = 2


class StatsCache:
    """Per-shop cache of computed stats: {(shop_id, name): (computed_at, value)}."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

    def get(self, shop_id: str, name: str, ttl: float) -> dict[str, Any] | None:
        with self._lock:
            hit = self._items.get((shop_id, name))
        if hit is None or time.time() - hit[0] > ttl:
            return None
        return dict(hit[1], cached_at=round(hit[0], 3))

    def put(self, shop_id: str, name: str, value: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self._items[(shop_id, name)] = (now, value)
        return dict(value, cached_at=round(now, 3))

    def last(self, shop_id: str) -> dict[str, dict[str, Any]]:
        """Whatever is cached for the shop, however old (for the fast endpoint)."""
        with self._lock:
            items = {name: (at, value) for (sid, name), (at, value) in self._items.items()
                     if sid == shop_id}
        return {name: dict(value, cached_at=round(at, 3)) for name, (at, value) in items.items()}


def register(r: Router, ctx: AppContext) -> None:
    cache = StatsCache()
    r.get("/api/dashboard", lambda req: overview(req, cache))
    r.get("/api/dashboard/stats", lambda req: stats(req, cache))


# --- the fast part ------------------------------------------------------------------


def setup_steps(status: dict[str, Any]) -> dict[str, Any]:
    """The four "Nasıl çalışıyor?" tiles, from the status object.

    Hesap: Etsy keys saved and accepted. Mağaza: signed in. Mockuplar: at least one
    mockup. Tasarım yükle: a template listing chosen, the last thing an upload needs.
    The first step not done is the current one.
    """
    state = status.get("state")
    setup = status.get("setup") or {}
    keys_ok = bool(setup.get("keys")) and state not in ("keys", "bad_keys")
    connected = bool(setup.get("connected")) and state != "reconnect"
    mockups = int(setup.get("mockups") or 0)
    template = bool(setup.get("template"))
    done = {
        "account": keys_ok,
        "shop": keys_ok and connected,
        "mockups": mockups > 0,
        "designs": template,
    }
    steps = []
    current: str | None = None
    for step_id in ("account", "shop", "mockups", "designs"):
        if done[step_id]:
            step_state = "done"
        elif current is None:
            step_state = "current"
            current = step_id
        else:
            step_state = "todo"
        steps.append({"id": step_id, "state": step_state})
    return {
        "steps": steps,
        "current": current,
        "complete": current is None,
        "checking": state == "checking",
        "problem": state if state in ("bad_keys", "reconnect") else None,
        "mockups": mockups,
        "template_title": setup.get("template_title"),
        "designs_pending": int(setup.get("designs_pending") or 0),
    }


def recent_activity(ctx: AppContext, limit: int = RECENT_LIMIT) -> list[dict[str, Any]]:
    """Running and queued jobs first, then the newest notifications."""
    items: list[dict[str, Any]] = []
    for job in ctx.jobs.list(active=True):
        summary = job.summary()
        items.append({
            "type": "job",
            "id": summary["id"],
            "kind": summary["kind"],
            "title_key": summary["title_key"],
            "params": summary["params"],
            "status": summary["status"],
            "progress": summary["progress"],
            "at": summary["started_at"] or summary["created_at"],
        })
    for note in ctx.notifications():
        if len(items) >= limit:
            break
        items.append({"type": "notification", **note})
    return items[:limit]


def _pinterest_summary() -> dict[str, Any] | None:
    """Pins waiting and due today, from the local queue file (no network)."""
    from datetime import date

    from ... import pinterest

    try:
        queue = pinterest.Queue.load()
    except Exception:  # noqa: BLE001 — a broken queue file is the Pinterest page's business
        return None
    counts = queue.counts()
    return {
        "pending": counts.get("pending", 0),
        "due": len(queue.due(date.today())),
        "attention": sum(counts.get(s, 0) for s in ("failed", "uncertain", "sending")),
    }


def overview(req: Request, cache: StatsCache) -> dict[str, Any]:
    ctx = req.ctx
    assert ctx is not None
    status = ctx.status
    return {
        "status": status,
        "setup": setup_steps(status),
        "recent": recent_activity(ctx),
        "stats": cache.last(ctx.shop_id) or None,
        "pinterest": _pinterest_summary(),
    }


# --- the numbers from Etsy --------------------------------------------------------------


def _money(value: Any) -> tuple[float, str | None]:
    """An Etsy Money object -> (amount, currency); anything else -> (0, None)."""
    if not isinstance(value, dict):
        return 0.0, None
    try:
        amount = float(value.get("amount") or 0) / float(value.get("divisor") or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0, None
    return amount, value.get("currency_code") or None


def month_start(now: datetime | None = None) -> datetime:
    """Midnight on the first day of this month, local time (the seller's computer)."""
    now = now or datetime.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def receipt_revenue(receipt: dict[str, Any]) -> tuple[float, str | None]:
    """What the buyer paid for items and shipping, after coupons and refunds; tax excluded.

    ShopReceipt (OAS): subtotal = total_price minus coupon discounts, no tax or shipping;
    total_shipping_cost; refunds[].amount. A fully refunded order counts as nothing.
    """
    if str(receipt.get("status") or "").lower() in ("canceled", "fully refunded"):
        return 0.0, None
    subtotal, currency = _money(receipt.get("subtotal"))
    shipping, ship_currency = _money(receipt.get("total_shipping_cost"))
    refunded = sum(_money(refund.get("amount"))[0] for refund in receipt.get("refunds") or []
                   if isinstance(refund, dict))
    return subtotal + shipping - refunded, currency or ship_currency


def _count_active(client: Any) -> dict[str, Any]:
    return {"value": client.count_listings("active")}


def _count_draft(client: Any) -> dict[str, Any]:
    return {"value": client.count_listings("draft")}


def _count_to_ship(client: Any) -> dict[str, Any]:
    # getShopReceipts filters (OAS): paid, not shipped, not canceled.
    return {"value": client.count_receipts(was_paid=True, was_shipped=False, was_canceled=False)}


def _seo_sample(client: Any) -> dict[str, Any]:
    from ... import seo

    scores = [seo.audit_listing(listing).score
              for listing in client.listings_by_shop("active", max_items=SEO_SAMPLE)]
    if not scores:
        return {"value": None, "sample": 0, "low": 0}
    return {
        "value": round(sum(scores) / len(scores)),
        "sample": len(scores),
        "low": sum(1 for score in scores if score < SEO_LOW),
    }


def _revenue(client: Any, shop_currency: str | None) -> dict[str, Any]:
    start = month_start()
    total = 0.0
    orders = 0
    currency: str | None = None
    receipts = client.receipts(
        max_items=REVENUE_MAX_RECEIPTS,
        min_created=int(start.timestamp()),
        was_paid=True,
        was_canceled=False,
    )
    for receipt in receipts:
        amount, cur = receipt_revenue(receipt)
        if cur is None and amount == 0:
            continue
        total += amount
        orders += 1
        currency = currency or cur
    return {
        "value": round(total, 2),
        "currency": currency or shop_currency or "USD",
        "orders": orders,
        "month": start.strftime("%Y-%m"),
        "partial": orders >= REVENUE_MAX_RECEIPTS,
    }


def _error(exc: BaseException) -> dict[str, Any]:
    return {"value": None, "error": describe(exc)}


def stats(req: Request, cache: StatsCache) -> dict[str, Any]:
    ctx = req.ctx
    assert ctx is not None
    refresh = req.bool_query("refresh")
    shop_id = ctx.shop_id
    try:
        client = ctx.client()
    except ApiError as exc:
        error = {"code": exc.code, "message": exc.message, "params": exc.params}
        return {
            "available": False,
            "error": error,
            "stats": {name: {"value": None, "error": error} for name in STAT_NAMES},
        }

    status = ctx.status
    if status.get("state") == "offline" and not refresh:
        # The status check just found Etsy unreachable: say so now instead of letting
        # every number time out in turn. "Yenile" (refresh=1) still tries.
        error = {"code": "offline", "message": status.get("detail") or "Etsy is not reachable.",
                 "params": {}}
        out = {name: cache.get(shop_id, name, float("inf")) or {"value": None, "error": error}
               for name in STAT_NAMES if name != "quota"}
        out["quota"] = {"value": status.get("quota_remaining"), "error": None}
        return {"available": True, "error": error, "stats": {name: out[name] for name in STAT_NAMES}}
    shop_currency = (status.get("shop") or {}).get("currency")
    work: dict[str, tuple[float, Callable[[], dict[str, Any]]]] = {
        "active": (COUNT_TTL, lambda: _count_active(client)),
        "draft": (COUNT_TTL, lambda: _count_draft(client)),
        "to_ship": (COUNT_TTL, lambda: _count_to_ship(client)),
        "seo": (SAMPLE_TTL, lambda: _seo_sample(client)),
        "revenue": (SAMPLE_TTL, lambda: _revenue(client, shop_currency)),
    }
    out: dict[str, dict[str, Any]] = {}
    todo: dict[str, Callable[[], dict[str, Any]]] = {}
    for name, (ttl, fn) in work.items():
        hit = None if refresh else cache.get(shop_id, name, ttl)
        if hit is not None:
            out[name] = hit
        else:
            todo[name] = fn

    def run(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with client.attempts(ATTEMPTS):
            return fn()

    if todo:
        # This thread holds the shop read lock until every worker is done, so the shop
        # cannot change while they read it.
        with ThreadPoolExecutor(max_workers=min(4, len(todo)),
                                thread_name_prefix="stallkit-dashboard") as pool:
            futures = {name: pool.submit(run, fn) for name, fn in todo.items()}
            for name, future in futures.items():
                try:
                    out[name] = cache.put(shop_id, name, dict(future.result(), error=None))
                except Exception as exc:  # noqa: BLE001 — one stat failing must not sink the rest
                    out[name] = _error(exc)

    quota = client.quota_remaining
    if quota is None:
        quota = ctx.status.get("quota_remaining")
    out["quota"] = {"value": quota, "error": None}

    errors = [out[name].get("error") for name in work if out[name].get("error")]
    common = None
    if errors and len(errors) == len(work) and len({e["code"] for e in errors}) == 1:
        common = errors[0]
    return {"available": True, "error": common, "stats": {name: out[name] for name in STAT_NAMES}}
