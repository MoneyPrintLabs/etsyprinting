"""HTTP client for the Etsy Open API v3.

Handles the four things every caller would otherwise reimplement badly:
throttling under the app's per-second ceiling (5 QPS on Personal Access, higher with
commercial access), retry with backoff that never repeats a non-idempotent write,
transparent token refresh on 401, and offset pagination.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx

from . import auth
from .config import API_BASE, MAX_PAGE_LIMIT, Config
from .errors import AuthError, EtsyApiError, ValidationError

MAX_ATTEMPTS = 5
# Etsy's search offset window is finite; walking past this returns errors, not pages.
MAX_SEARCH_OFFSET = 12_000


def encode_form(data: dict[str, Any]) -> dict[str, str]:
    """Shape a dict for Etsy's application/x-www-form-urlencoded endpoints.

    Etsy documents list fields (tags, materials, image_ids) as *comma-separated
    strings*, not repeated keys — sending repeated keys silently drops all but one
    value. Booleans must be lowercase literals. None means "leave unset".
    """
    out: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            if not value:
                continue
            out[key] = ",".join(str(v) for v in value)
        else:
            out[key] = str(value)
    return out


class RateLimiter:
    """Sliding-window limiter. Thread-safe so callers may parallelise later."""

    def __init__(self, per_second: float) -> None:
        self.per_second = max(0.5, per_second)
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                while self._times and now - self._times[0] >= 1.0:
                    self._times.popleft()
                if len(self._times) < self.per_second:
                    self._times.append(now)
                    return
                sleep_for = 1.0 - (now - self._times[0])
                if sleep_for > 0:
                    time.sleep(sleep_for)


class EtsyClient:
    def __init__(
        self,
        config: Config,
        *,
        token: auth.Token | None = None,
        require_auth: bool = True,
    ) -> None:
        self.config = config
        self.token = token if token is not None else auth.load_token()
        if require_auth and self.token is None:
            raise AuthError("Not authenticated. Run: stallkit auth login")
        self.limiter = RateLimiter(config.rate_per_sec)
        self._http = httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))
        self._shop_id: int | None = config.shop_id
        self._me: dict[str, Any] | None = None
        self.quota_remaining: int | None = None

    def __enter__(self) -> EtsyClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # --- core request machinery -------------------------------------------------

    def _headers(self, *, authed: bool) -> dict[str, str]:
        headers = {
            # Both halves, colon-joined. See Config.api_key_header for why.
            "x-api-key": self.config.api_key_header,
            "Accept": "application/json",
            "User-Agent": "stallkit/0.1 (+https://github.com/MoneyPrintLabs/etsyprinting)",
        }
        if authed:
            if self.token is None:
                raise AuthError("This command needs authentication. Run: stallkit auth login")
            if self.token.expired:
                self.token = auth.refresh(self.token, self.config)
            headers["Authorization"] = f"Bearer {self.token.access_token}"
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        authed: bool = True,
        retry: bool | None = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        body = encode_form(form) if form is not None else None
        refreshed_once = False

        # Etsy has no idempotency key, so a re-sent write is a real duplicate: a second
        # draft listing, a second image, a second shipment plus a second "your order
        # shipped" email to the buyer. A read timeout does NOT mean Etsy rejected the
        # request — it may have committed. So writes are only retried when the request
        # provably never arrived (connect failure) or was provably refused (429).
        idempotent = method.upper() in {"GET", "HEAD", "OPTIONS"}
        may_retry = idempotent if retry is None else retry

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.limiter.acquire()
            try:
                resp = self._http.request(
                    method,
                    url,
                    params=clean_params or None,
                    data=body,
                    json=json_body,
                    files=files,
                    headers=self._headers(authed=authed),
                )
            except httpx.HTTPError as exc:
                # A connection that was never established cannot have changed anything.
                never_arrived = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                if attempt == MAX_ATTEMPTS or not (may_retry or never_arrived):
                    message = f"network error: {exc}"
                    if not idempotent and not never_arrived:
                        message += (
                            " — the request may still have been accepted by Etsy. "
                            "Check your shop before running this again."
                        )
                    raise EtsyApiError(0, message, method=method, path=path) from exc
                time.sleep(self._backoff(attempt))
                continue

            remaining = resp.headers.get("x-remaining-today")
            if remaining and remaining.isdigit():
                self.quota_remaining = int(remaining)

            if resp.status_code < 300:
                if not resp.content:
                    return None
                try:
                    return resp.json()
                except ValueError:
                    return resp.text

            # An expired token that we did not predict — refresh once, then retry.
            if resp.status_code == 401 and authed and not refreshed_once and self.token:
                refreshed_once = True
                self.token = auth.refresh(self.token, self.config)
                continue

            error = EtsyApiError(
                resp.status_code,
                self._error_message(resp),
                method=method,
                path=path,
                body=resp.text[:2000],
            )
            # 429 is always safe to retry: it means Etsy refused, not that it acted.
            # A 5xx on a write may mean the write landed, so do not repeat it.
            retryable = resp.status_code == 429 or (resp.status_code >= 500 and may_retry)
            if not retryable or attempt == MAX_ATTEMPTS:
                raise error

            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else self._backoff(attempt)
            time.sleep(delay)

        raise EtsyApiError(0, "exhausted retries", method=method, path=path)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, (2 ** (attempt - 1)) + random.uniform(0, 0.6))

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        try:
            payload = resp.json()
        except ValueError:
            return (resp.text or resp.reason_phrase or "unknown error").strip()[:400]
        if isinstance(payload, dict):
            for key in ("error", "error_description", "message"):
                if payload.get(key):
                    return str(payload[key])[:400]
        return str(payload)[:400]

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw: Any) -> Any:
        return self.request("PATCH", path, **kw)

    def put(self, path: str, **kw: Any) -> Any:
        return self.request("PUT", path, **kw)

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_items: int | None = None,
        authed: bool = True,
        page_size: int = MAX_PAGE_LIMIT,
    ) -> Iterator[dict[str, Any]]:
        """Walk an offset-paginated collection, yielding one record at a time."""
        offset = 0
        seen = 0
        page_size = min(page_size, MAX_PAGE_LIMIT)
        while True:
            page_params = dict(params or {})
            page_params.update(limit=page_size, offset=offset)
            payload = self.get(path, params=page_params, authed=authed)
            results = (payload or {}).get("results") or []
            if not results:
                return
            for item in results:
                yield item
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            offset += len(results)
            total = (payload or {}).get("count")
            if isinstance(total, int) and offset >= total:
                return
            if len(results) < page_size or offset >= MAX_SEARCH_OFFSET:
                return

    # --- identity ---------------------------------------------------------------

    def me(self) -> dict[str, Any]:
        if self._me is None:
            self._me = self.get("/users/me")
        return self._me

    def shop_id(self) -> int:
        """Resolve the shop to operate on: ETSY_SHOP_ID if set, else the token owner's shop."""
        if self._shop_id is not None:
            return self._shop_id
        user_id = self.me().get("user_id") or (self.token.user_id if self.token else None)
        if not user_id:
            raise AuthError("Could not determine your Etsy user id. Try: stallkit auth login")
        shop = self.get(f"/users/{user_id}/shops")
        # Etsy has returned this either as a bare shop object or wrapped in results.
        if isinstance(shop, dict) and shop.get("results"):
            shop = shop["results"][0]
        shop_id = (shop or {}).get("shop_id")
        if not shop_id:
            raise AuthError(
                "No shop is attached to this Etsy account. Open a shop first, "
                "or set ETSY_SHOP_ID in your .env."
            )
        self._shop_id = int(shop_id)
        return self._shop_id

    def shop(self) -> dict[str, Any]:
        return self.get(f"/shops/{self.shop_id()}")

    # --- endpoints used across commands ----------------------------------------

    def ping(self) -> Any:
        """Key-only health check — proves the keystring works before OAuth exists."""
        return self.get("/openapi-ping", authed=False)

    def shipping_profiles(self) -> list[dict[str, Any]]:
        payload = self.get(f"/shops/{self.shop_id()}/shipping-profiles")
        return (payload or {}).get("results") or []

    def return_policies(self) -> list[dict[str, Any]]:
        payload = self.get(f"/shops/{self.shop_id()}/policies/return")
        return (payload or {}).get("results") or []

    def shop_sections(self) -> list[dict[str, Any]]:
        payload = self.get(f"/shops/{self.shop_id()}/sections")
        return (payload or {}).get("results") or []

    def shipping_carriers(self, origin_country_iso: str) -> list[dict[str, Any]]:
        payload = self.get(
            "/shipping-carriers", params={"origin_country_iso": origin_country_iso}
        )
        return (payload or {}).get("results") or []

    def taxonomy_nodes(self) -> list[dict[str, Any]]:
        payload = self.get("/seller-taxonomy/nodes", authed=False)
        return (payload or {}).get("results") or []

    def listings_by_shop(
        self, state: str = "active", *, includes: Sequence[str] | None = None, max_items: int | None = None
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"state": state}
        if includes:
            params["includes"] = ",".join(includes)
        yield from self.paginate(
            f"/shops/{self.shop_id()}/listings", params=params, max_items=max_items
        )

    def listing(self, listing_id: int, *, includes: Sequence[str] | None = None) -> dict[str, Any]:
        """One listing by id. Key-only endpoint — no OAuth scope needed."""
        params = {"includes": ",".join(includes)} if includes else None
        return self.get(f"/listings/{listing_id}", params=params, authed=False)

    def create_draft_listing(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self.post(f"/shops/{self.shop_id()}/listings", form=fields)

    def update_listing(self, listing_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self.patch(f"/shops/{self.shop_id()}/listings/{listing_id}", form=fields)

    def upload_listing_image(
        self, listing_id: int, image: Path, *, rank: int = 1, alt_text: str = ""
    ) -> dict[str, Any]:
        # Refuse before reading. A file Etsy will not take should not be pulled into
        # memory first, and the refusal has to land before the request, not after —
        # by upload time the draft already exists and the row can only be "partial".
        mime = _mime_for(image)
        with image.open("rb") as handle:
            files = {"image": (image.name, handle.read(), mime)}
        data = {"rank": str(rank)}
        if alt_text:
            data["alt_text"] = alt_text[:250]
        return self.request(
            "POST",
            f"/shops/{self.shop_id()}/listings/{listing_id}/images",
            files={**files, **{k: (None, v) for k, v in data.items()}},
        )

    def receipts(self, *, max_items: int | None = None, **filters: Any) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/shops/{self.shop_id()}/receipts", params=filters, max_items=max_items
        )

    def create_receipt_shipment(self, receipt_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        # Note: unlike listings, this endpoint takes JSON, not form encoding.
        return self.post(
            f"/shops/{self.shop_id()}/receipts/{receipt_id}/tracking",
            json_body={k: v for k, v in payload.items() if v is not None},
        )

    def search_active_listings(
        self, *, keywords: str, max_items: int = 100, **filters: Any
    ) -> Iterator[dict[str, Any]]:
        """Public marketplace search. Needs the API key but no OAuth token."""
        params = {"keywords": keywords, **filters}
        yield from self.paginate(
            "/listings/active", params=params, max_items=max_items, authed=False
        )


# What Etsy's listing-image endpoint accepts, and nothing else. WEBP is deliberately
# absent even though Pillow reads it: Etsy refuses it, and mapping it to image/webp
# would make stallkit look like it supported a format that fails on arrival. Nothing
# is ever sent under a guessed type such as application/octet-stream.
_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
}

# The same fact as a suffix set, for callers deciding about a file before a listing
# exists. `drop.workspace.IMAGE_SUFFIXES` is the wider *input* set and is not this.
UPLOADABLE_SUFFIXES = frozenset(_MIME)

# Etsy refuses a listing image over 20MB.
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def image_problem(path: Path) -> str | None:
    """Why Etsy would refuse this file, or None if it would take it.

    Suffix and size only, deliberately: this runs over every image of every row
    inside `listings.prepare()`, and the question it answers is "would the endpoint
    accept this", not "are the pixels intact". Decoding belongs further in, where a
    batch is being read anyway.
    """
    suffix = path.suffix.lower()
    if suffix not in UPLOADABLE_SUFFIXES:
        return (
            f"{path.name}: Etsy accepts JPG, PNG and GIF listing images, not "
            f"{suffix or 'a file with no extension'} — save it as a .jpg and re-run"
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"{path.name}: cannot be read ({exc})"
    if size == 0:
        return f"{path.name} is empty"
    if size > MAX_IMAGE_BYTES:
        return (
            f"{path.name} is {size / 1024 / 1024:.1f}MB and Etsy's limit for a listing "
            f"image is {MAX_IMAGE_BYTES // 1024 // 1024}MB"
        )
    return None


def _mime_for(path: Path) -> str:
    """The Content-Type for an upload, refusing anything Etsy would not accept."""
    problem = image_problem(path)
    if problem:
        raise ValidationError(problem)
    return _MIME[path.suffix.lower()]
