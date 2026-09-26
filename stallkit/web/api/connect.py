"""Mağaza Bağlantısı: the Etsy app keys, connecting the shop, disconnecting it.

    GET  /api/connect/info        what is saved (never the secret) and what Etsy granted
    POST /api/connect/keys        save keystring + shared secret (+ callback), ask Etsy
    POST /api/connect/start       open the callback listener, start the `connect` job
    POST /api/connect/disconnect  forget this computer's sign-in (token.json)

The consent happens on Etsy's own page, in a tab the browser opened for it. Etsy sends
that tab to the one-shot callback listener (by default
http://localhost:3003/oauth/redirect), which hands the code to the job and sends the
tab on to /oauth-done in the app. The code never leaves the listener.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import urllib.parse
from typing import TYPE_CHECKING, Any

from ... import auth, shops
from ...config import DEFAULT_SCOPES, Config, split_credential
from ...desktop import settings
from ...errors import AuthError, AuthUnreachable, ConfigError, EtsyApiError, StallKitError
from ..jobs import JobCancelled
from ..router import ApiError, Request

if TYPE_CHECKING:  # pragma: no cover
    from ..context import AppContext
    from ..jobs import Job
    from ..router import Router

log = logging.getLogger("stallkit.web")

# Every OAuth scope Etsy's Open API v3 defines, as listed under
# components.securitySchemes.oauth2 in https://www.etsy.com/openapi/generated/oas/3.0.0.json.
# An extra scope outside this list is refused here instead of on Etsy's consent page.
ETSY_SCOPES = (
    "address_r", "address_w", "email_r", "listings_d", "listings_r", "listings_w",
    "profile_r", "profile_w", "shops_r", "shops_w", "transactions_r", "transactions_w",
)
CONNECT_KIND = "connect"
CONNECT_TIMEOUT = 300.0  # seconds the listener waits for the person to answer Etsy
LISTEN_WAIT = 5.0  # how long /start waits for the listener to accept connections
BIND_FAILS_WITHIN = 2.0  # a listener that gives up this fast never got its port
DONE_PATH = "/oauth-done"
MAX_KEY_LEN = 200

# _CallbackHandler.return_url is class state: one connect flow sets it at a time.
_listener_lock = threading.Lock()


def register(r: Router, ctx: AppContext) -> None:
    r.get("/api/connect/info", info)
    r.post("/api/connect/keys", save_keys)
    r.post("/api/connect/start", start)
    r.post("/api/connect/disconnect", disconnect)


def _ctx(req: Request) -> AppContext:
    assert req.ctx is not None
    return req.ctx


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def _saved_keys() -> tuple[str, str]:
    return split_credential(
        settings.current("ETSY_KEYSTRING"), settings.current("ETSY_SHARED_SECRET")
    )


def _requested_scopes() -> list[str]:
    raw = settings.current("ETSY_SCOPES")
    return raw.split() if raw else list(DEFAULT_SCOPES)


def _active_connect(ctx: AppContext) -> Job | None:
    jobs = ctx.jobs.list(kind=CONNECT_KIND, active=True)
    return jobs[0] if jobs else None


# --- GET /api/connect/info -------------------------------------------------------------


def info(req: Request) -> dict[str, Any]:
    """Everything the page shows about the connection, read from disk (no network)."""
    ctx = _ctx(req)
    keystring, secret = _saved_keys()
    saved_redirect = settings.current("ETSY_REDIRECT_URI")
    redirect = saved_redirect or settings.ETSY_REDIRECT_DEFAULT
    problem = ""
    try:
        auth.validate_redirect_uri(redirect)
    except AuthError as exc:
        problem = _first_line(exc)
    try:
        token = auth.load_token()
    except ConfigError:
        token = None
    requested = _requested_scopes()
    name = shops.current().name or None
    job = _active_connect(ctx)
    return {
        "keys": bool(keystring and secret),
        "keystring_prefix": keystring[:6],
        "secret_length": len(secret),
        "redirect_uri": redirect,
        "redirect_saved": bool(saved_redirect),
        "redirect_default": settings.ETSY_REDIRECT_DEFAULT,
        "redirect_ok": not problem,
        "redirect_problem": problem,
        "redirect_local": auth.is_loopback(redirect),
        "scopes_requested": requested,
        "scopes_default": list(DEFAULT_SCOPES),
        "scopes_granted": list(token.scopes) if token else [],
        "missing_scopes": list(token.missing_scopes(tuple(requested))) if token else [],
        "known_scopes": list(ETSY_SCOPES),
        "connected": token is not None,
        "shop_name": ctx.anonymise(name) if name else None,
        "job": job.to_dict() if job else None,
    }


# --- POST /api/connect/keys -------------------------------------------------------------


def _text(body: dict[str, Any], name: str) -> str:
    value = body.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError(422, "invalid", f"{name} must be text", field=name)
    return value.strip()


def _check_key(name: str, value: str) -> None:
    if len(value) > MAX_KEY_LEN or any(ch.isspace() for ch in value):
        raise ApiError(422, "invalid_key", f"That does not look like an Etsy {name}.", field=name)


def _verify(ctx: AppContext) -> tuple[str, str]:
    """Ask Etsy whether it accepts the saved keys: ("ok"|"rejected"|"offline"|"unknown", why)."""
    client = ctx.client(require_auth=False)
    try:
        with client.attempts(2):
            client.ping()
    except EtsyApiError as exc:
        if exc.status == 0:
            return "offline", exc.message
        if exc.status in (401, 403):
            return "rejected", exc.message
        return "unknown", _first_line(exc)
    return "ok", ""


def save_keys(req: Request) -> dict[str, Any]:
    """Save the app keys (and callback), then check them with a key-only call.

    An empty keystring or secret keeps the saved one, so the callback can be changed
    without typing the keys again; a new keystring needs its own secret. A keystring
    from another app makes the old sign-in useless, so it is removed.
    """
    ctx = _ctx(req)
    body = req.json_object()
    typed_key, typed_secret = split_credential(_text(body, "keystring"), _text(body, "shared_secret"))
    typed_redirect = _text(body, "redirect_uri")
    saved_key, saved_secret = _saved_keys()
    keystring = typed_key or saved_key
    secret = typed_secret or (saved_secret if keystring == saved_key else "")
    if not keystring or not secret:
        missing = "keystring" if not keystring else "shared_secret"
        raise ApiError(422, "need_both_keys", "Both the keystring and the shared secret are needed.",
                       field=missing)
    _check_key("keystring", keystring)
    _check_key("shared_secret", secret)
    redirect = typed_redirect or settings.current("ETSY_REDIRECT_URI") or settings.ETSY_REDIRECT_DEFAULT
    try:
        auth.validate_redirect_uri(redirect)
    except AuthError as exc:
        raise ApiError(422, "bad_redirect", _first_line(exc), field="redirect_uri") from exc
    if ctx.jobs.busy():
        raise ApiError(409, "busy", "Wait for the running task to finish first.")

    other_app = bool(saved_key) and saved_key != keystring
    settings.save({
        "ETSY_KEYSTRING": keystring,
        "ETSY_SHARED_SECRET": secret,
        "ETSY_REDIRECT_URI": redirect,
    })
    token_cleared = other_app and auth.clear_token()
    ctx.reset_client()
    check, reason = _verify(ctx)
    status = ctx.refresh_status(force=True)
    return {
        "saved": True,
        "check": check,
        "reason": reason,
        "token_cleared": token_cleared,
        "keystring_prefix": keystring[:6],
        "secret_length": len(secret),
        "redirect_uri": redirect,
        "status": status,
    }


# --- POST /api/connect/start --------------------------------------------------------------


class _CancelFlag:
    """The one thing _capture_via_listener asks of its cancel Event, answered by the job."""

    def __init__(self, job: Job) -> None:
        self._job = job

    def is_set(self) -> bool:
        return self._job.cancelled


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _stop_earlier_attempt(ctx: AppContext) -> None:
    """An earlier connect still waiting for Etsy is replaced; any other task blocks."""
    active = ctx.jobs.list(active=True)
    if any(job.kind != CONNECT_KIND for job in active):
        raise ApiError(409, "busy", "Wait for the running task to finish first.")
    for job in active:
        try:
            ctx.jobs.cancel(job.id)
        except ValueError:  # pragma: no cover - connect jobs are cancellable
            pass
        job.wait(LISTEN_WAIT)


def _extra_scopes(body: dict[str, Any]) -> list[str]:
    extra = body.get("extra_scopes") or []
    if not isinstance(extra, list) or not all(isinstance(s, str) for s in extra):
        raise ApiError(422, "invalid", "extra_scopes must be a list of scope names",
                       field="extra_scopes")
    unknown = [s for s in extra if s not in ETSY_SCOPES]
    if unknown:
        raise ApiError(422, "unknown_scope", f"Etsy has no scope {unknown[0]!r}.",
                       field="extra_scopes", scope=unknown[0])
    return extra


def start(req: Request) -> dict[str, Any]:
    """Open the listener on the callback port and start the `connect` job.

    Answers only once the listener accepts connections, so the browser can be sent to
    Etsy straight away: {job_id, url, job}.
    """
    ctx = _ctx(req)
    body = req.json_object()
    extra = _extra_scopes(body)
    _stop_earlier_attempt(ctx)
    try:
        config = Config.load()
    except ConfigError as exc:
        raise ApiError(409, "setup_needed", _first_line(exc), step="keys") from exc

    changes: dict[str, str] = {}
    wanted = tuple(dict.fromkeys([*config.scopes, *extra]))
    if wanted != config.scopes:
        changes["ETSY_SCOPES"] = " ".join(wanted)
    if not config.redirect_uri:
        changes["ETSY_REDIRECT_URI"] = settings.ETSY_REDIRECT_DEFAULT
    if changes:
        settings.save(changes)
        config = Config.load()

    try:
        auth.validate_redirect_uri(config.redirect_uri)
    except AuthError as exc:
        raise ApiError(422, "bad_redirect", _first_line(exc), field="redirect_uri") from exc
    if not auth.is_loopback(config.redirect_uri):
        raise ApiError(
            409, "callback_not_local",
            "Only an http://localhost callback can be caught on this computer.",
            redirect_uri=config.redirect_uri, suggested=settings.ETSY_REDIRECT_DEFAULT,
        )
    port = urllib.parse.urlparse(config.redirect_uri).port or 80
    if port == ctx.port or not auth.port_is_free(port):
        raise ApiError(409, "port_in_use", f"Port {port} is already in use.", port=port)

    request = auth.build_authorization_url(config)
    return_url = f"http://localhost:{ctx.port}{DONE_PATH}"

    def work(job: Job) -> dict[str, Any]:
        return _connect(ctx, job, config, request, port, return_url)

    job = ctx.jobs.start(
        CONNECT_KIND, "connect:job.title", work,
        params={"port": port}, cancellable=True, refresh_status=True,
    )
    _wait_until_listening(job, port)
    return {"job_id": job.id, "url": request.url, "job": job.summary()}


def _wait_until_listening(job: Job, port: int) -> None:
    deadline = time.monotonic() + LISTEN_WAIT
    while time.monotonic() < deadline:
        if job.wait(0.02):
            if job.status == "cancelled":
                raise ApiError(409, "cancelled", "Cancelled.")
            error = job.error or {"code": "internal", "message": "", "params": {}}
            raise ApiError(409, error["code"], error["message"], **(error.get("params") or {}))
        if _listening(port):
            return
    log.warning("the Etsy callback listener on port %s did not answer in time", port)


def _phase(job: Job, phase: str, step: int) -> None:
    job.set_state(phase=phase)
    job.progress(step, 4, label=phase)
    job.emit("phase", phase=phase)


def _listener_error(job: Job, exc: AuthError, port: int, elapsed: float) -> BaseException:
    """Why the listener came back without a code, as a code the page can explain."""
    result = dict(auth._CallbackHandler.result)
    if job.cancelled:
        return JobCancelled()
    if "error" in result:
        return ApiError(400, "consent_denied", _first_line(exc), etsy_error=result["error"])
    if result:
        return ApiError(400, "state_mismatch", _first_line(exc))
    if elapsed < BIND_FAILS_WITHIN:
        return ApiError(409, "port_in_use", _first_line(exc), port=port)
    return ApiError(408, "connect_timeout", _first_line(exc), minutes=int(CONNECT_TIMEOUT // 60))


def _connect(
    ctx: AppContext, job: Job, config: Config, request: auth.AuthRequest, port: int,
    return_url: str,
) -> dict[str, Any]:
    job.set_state(url=request.url, port=port)
    _phase(job, "opened", 1)
    started = time.monotonic()
    with _listener_lock:
        auth._CallbackHandler.return_url = return_url
        try:
            code = auth._capture_via_listener(
                request, config, port, CONNECT_TIMEOUT, _CancelFlag(job)  # type: ignore[arg-type]
            )
        except AuthError as exc:
            raise _listener_error(job, exc, port, time.monotonic() - started) from exc
        finally:
            auth._CallbackHandler.return_url = None
    job.check_cancel()

    _phase(job, "code_received", 2)
    try:
        token = auth.exchange_code(config, code, request.verifier)
    except AuthUnreachable:
        raise
    except AuthError as exc:
        raise ApiError(502, "token_refused", _first_line(exc)) from exc
    ctx.reset_client()

    _phase(job, "fetching_shop", 3)
    shop = ctx.client().shop()
    name = str(shop.get("shop_name") or "")
    try:
        shops.remember(name, shop.get("shop_id"))
    except (OSError, StallKitError):
        log.warning("could not remember the shop name")
    ctx.refresh_status(force=True)
    shown = ctx.anonymise(name) if name else None
    if shown:
        ctx.notify("connect", "notify.connected", {"shop": shown}, tone="success",
                   link="/kurulum/magaza")
    else:
        ctx.notify("connect", "notify.connected_plain", tone="success", link="/kurulum/magaza")
    _phase(job, "done", 4)
    return {
        "shop_name": shown,
        "scopes": list(token.scopes),
        "missing_scopes": list(token.missing_scopes(config.scopes)),
    }


# --- POST /api/connect/disconnect ------------------------------------------------------------


def disconnect(req: Request) -> dict[str, Any]:
    """Delete token.json. The keys stay; Etsy's own record of the app is not touched."""
    ctx = _ctx(req)
    if ctx.jobs.busy():
        raise ApiError(409, "busy", "Wait for the running task to finish first.")
    removed = auth.clear_token()
    ctx.reset_client()
    return {"removed": removed, "status": ctx.refresh_status(force=True)}
