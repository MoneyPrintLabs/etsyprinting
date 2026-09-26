"""AppContext: everything a request handler or a job needs, in one object.

Phase-2 handlers receive it as `req.ctx` (and as `ctx` in `register(r, ctx)`):

    client = ctx.client()                # shared EtsyClient for the open shop (needs a sign-in)
    client = ctx.client(require_auth=False)   # key-only calls (ping, public listing)
    ws = ctx.workspace()                 # drop.workspace.Workspace, created on first use
    prefs = ctx.shop_prefs(); prefs["x"] = 1; ctx.save_shop_prefs(prefs)
    ctx.app_prefs() / ctx.save_app_prefs(dict)       # desktop.json (language, anonymise, ...)
    ctx.status                           # cached status object (spec 4.2)
    ctx.refresh_status(force=True)       # re-check now, in this thread
    ctx.set_status_soon()                # re-check shortly, in the background
    ctx.jobs.start(kind, title_key, fn, params=..., cancellable=True)
    ctx.events.publish(topic, data)      # SSE
    ctx.notify("orders", "shipped", {"n": 3}, tone="success", link="/siparisler")
    ctx.anonymise("Real Shop Name")      # "Mağaza 1" / "Shop 1" when the hide-names pref is on
    ctx.language                         # "tr" | "en"
    ctx.reset_client()                   # after saving keys, connecting or disconnecting

Which shop is open is process-wide state (os.environ, see desktop.settings.use_shop).
`shop_lock` makes that safe: every API handler and every job holds it for reading,
and only a shop switch takes it for writing.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import __version__, shops
from ..config import Config, home_dir
from ..desktop import settings
from ..errors import (
    AuthError,
    AuthUnreachable,
    ConfigError,
    EtsyApiError,
    StallKitError,
)
from . import i18n
from .events import EventHub
from .jobs import JobRunner
from .router import ApiError

log = logging.getLogger("stallkit.web")

# The state names of the status object.
CHECKING, KEYS, BAD_KEYS, DISCONNECTED, CONNECTED, RECONNECT, OFFLINE, ERROR = (
    "checking", "keys", "bad_keys", "disconnected", "connected", "reconnect", "offline", "error",
)
STATUS_DEBOUNCE = 10.0
SWITCH_WAIT = 10.0
NOTIFICATIONS_FILE = "notifications.json"
KEEP_NOTIFICATIONS = 50
TONES = ("info", "success", "warning", "danger")


class ShopLock:
    """A read/write lock, re-entrant per thread, readers first.

    Readers never wait for each other. A writer waits until no other thread holds
    the lock; `write(timeout)` raises ApiError 409 busy when that takes too long.
    The writing thread may also take the read side (a switch that re-checks status).
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers: dict[int, int] = {}
        self._writer: int | None = None
        self._write_depth = 0

    @contextmanager
    def read(self) -> Iterator[None]:
        me = threading.get_ident()
        with self._cond:
            while self._writer is not None and self._writer != me:
                self._cond.wait()
            self._readers[me] = self._readers.get(me, 0) + 1
        try:
            yield
        finally:
            with self._cond:
                left = self._readers[me] - 1
                if left:
                    self._readers[me] = left
                else:
                    del self._readers[me]
                self._cond.notify_all()

    @contextmanager
    def write(self, timeout: float | None = None) -> Iterator[None]:
        me = threading.get_ident()
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while (self._writer is not None and self._writer != me) or any(
                ident != me for ident in self._readers
            ):
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise ApiError(409, "busy", "Something else is using the shop right now.")
                self._cond.wait(remaining)
            self._writer = me
            self._write_depth += 1
        try:
            yield
        finally:
            with self._cond:
                self._write_depth -= 1
                if not self._write_depth:
                    self._writer = None
                self._cond.notify_all()


def _empty_status(state: str = CHECKING) -> dict[str, Any]:
    return {
        "state": state,
        "detail": "",
        "checked_at": None,
        "shop": {"id": "", "name": None, "etsy_shop_id": None, "currency": None},
        "setup": {
            "keys": False,
            "connected": False,
            "workspace": "",
            "mockups": 0,
            "mockups_calibrated": 0,
            "template": False,
            "template_title": None,
            "designs_pending": 0,
        },
        "scopes": [],
        "quota_remaining": None,
    }


class AppContext:
    def __init__(
        self,
        *,
        token: str = "",
        port: int = 0,
        transport: Any = None,
        check_status: bool = True,
    ) -> None:
        """`transport` (an httpx transport) is only for tests: every EtsyClient the
        context makes sends through it. check_status=False skips the first status
        check at start (tests that want to control it)."""
        self.token = token
        self.port = port
        self.version = __version__
        self.transport = transport
        self.started_at = time.time()
        self.shop_lock = ShopLock()
        self.events = EventHub()
        self.jobs = JobRunner(self)
        self.on_quit: Any = None  # set by the launcher: a no-argument callable
        self._client_lock = threading.Lock()
        self._client: Any = None
        self._client_key: tuple | None = None
        self._retired: list[Any] = []
        self._timers: list[threading.Timer] = []
        self._workspaces_made: set[str] = set()
        self._status_lock = threading.Lock()
        self._check_lock = threading.Lock()
        self._status: dict[str, Any] = _empty_status()
        self._status_generation = 0
        self._first_check = threading.Event()
        self._soon_timer: threading.Timer | None = None
        self._notify_lock = threading.Lock()
        self.closed = False
        self._system_language: str | None = None
        self._open_saved_shop()
        if check_status:
            self.set_status_soon(0.0)

    # --- identity -------------------------------------------------------------------

    @property
    def instance(self) -> str:
        """sha256(session token)[:16]: names this running app without revealing the token."""
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()[:16]

    @property
    def language(self) -> str:
        """"tr" | "en": the saved choice, else the system's language (asked once)."""
        saved = i18n.normalise(self.app_prefs().get("language"))
        if saved:
            return saved
        if self._system_language is None:
            self._system_language = i18n.detect_language()
        return self._system_language

    @property
    def anonymise_names(self) -> bool:
        return bool(self.app_prefs().get("anonymise", False))

    # --- preferences --------------------------------------------------------------------

    def app_prefs(self) -> dict[str, Any]:
        """desktop.json in the base home: language, anonymise, open shop. A fresh dict."""
        return settings.load_app_prefs()

    def save_app_prefs(self, prefs: dict[str, Any]) -> None:
        settings.save_app_prefs(prefs)

    def shop_prefs(self) -> dict[str, Any]:
        """desktop-shop.json in the open shop's home: workspace, template listing, ..."""
        return settings.load_shop_prefs()

    def save_shop_prefs(self, prefs: dict[str, Any]) -> None:
        settings.save_shop_prefs(prefs)

    def update_shop_prefs(self, **changes: Any) -> dict[str, Any]:
        """Merge `changes` into the shop prefs (None removes a key) and save them."""
        prefs = self.shop_prefs()
        for key, value in changes.items():
            if value is None:
                prefs.pop(key, None)
            else:
                prefs[key] = value
        self.save_shop_prefs(prefs)
        return prefs

    # --- shops ------------------------------------------------------------------------

    def _open_saved_shop(self) -> None:
        """Reopen the shop that was open last time, if it still exists."""
        wanted = str(self.app_prefs().get("shop") or "")
        known = {shop.id for shop in shops.all_shops()}
        try:
            settings.use_shop(wanted if wanted in known else "")
        except ConfigError:
            settings.use_shop("")

    @property
    def shop_id(self) -> str:
        """The open shop's id; "" is the first (base) shop."""
        try:
            return shops.current().id
        except ConfigError:
            return ""

    def shop_index(self, shop_id: str | None = None) -> int:
        wanted = self.shop_id if shop_id is None else shop_id
        ids = [shop.id for shop in shops.all_shops()]
        return ids.index(wanted) if wanted in ids else 0

    def shop_label(self, index: int) -> str:
        """"Mağaza 2" / "Shop 2" for the shop at `index` (0-based)."""
        word = "Mağaza" if self.language == "tr" else "Shop"
        return f"{word} {index + 1}"

    def anonymise(self, name: str | None, index: int | None = None) -> str | None:
        """`name`, or a "Mağaza N" label instead when the hide-names preference is on.

        `index` is the shop's position (0-based); default: the open shop.
        """
        if not self.anonymise_names:
            return name
        return self.shop_label(self.shop_index() if index is None else index)

    def shops_list(self) -> list[dict[str, Any]]:
        """Every shop: id, name (hidden when anonymising), label, connected, current."""
        current = self.shop_id
        hide = self.anonymise_names
        out = []
        for index, shop in enumerate(shops.all_shops()):
            real = shop.name or None
            name = self.shop_label(index) if hide and real else real
            out.append({
                "id": shop.id,
                "name": name,
                "label": name or self.shop_label(index),
                "connected": shop.connected,
                "current": shop.id == current,
            })
        return out

    def switch_shop(self, shop_id: str) -> None:
        """Open another shop. 409 busy while a job is queued or running."""
        if self.jobs.busy():
            raise ApiError(409, "busy", "Wait for the running task to finish first.")
        known = {shop.id for shop in shops.all_shops()}
        if shop_id not in known:
            raise ApiError(404, "not_found", f"There is no shop {shop_id!r}.")
        with self.shop_lock.write(timeout=SWITCH_WAIT):
            if self.jobs.busy():
                raise ApiError(409, "busy", "Wait for the running task to finish first.")
            self._select(shop_id)
        self.events.publish("shop", {"id": shop_id})
        self.set_status_soon(0.0)

    def _select(self, shop_id: str) -> None:
        """Make `shop_id` the open shop. Caller holds the write lock."""
        settings.use_shop(shop_id)
        prefs = self.app_prefs()
        prefs["shop"] = shop_id
        self.save_app_prefs(prefs)
        self.reset_client()
        with self._status_lock:
            self._status_generation += 1
            self._status = _empty_status()
        self.events.publish("status", self.public_status())

    def add_shop(self) -> str:
        """Create an empty shop and open it. Returns its id."""
        if self.jobs.busy():
            raise ApiError(409, "busy", "Wait for the running task to finish first.")
        with self.shop_lock.write(timeout=SWITCH_WAIT):
            if self.jobs.busy():
                raise ApiError(409, "busy", "Wait for the running task to finish first.")
            shop = shops.add()
            self._select(shop.id)
        self.events.publish("shop", {"id": shop.id})
        self.set_status_soon(0.0)
        return shop.id

    def remove_shop(self, shop_id: str) -> None:
        """Delete an extra shop's home (keys, token, prefs). The base shop cannot go."""
        if not shop_id:
            raise ApiError(400, "cannot_remove_base", "The first shop cannot be removed.")
        known = {shop.id for shop in shops.all_shops()}
        if shop_id not in known:
            raise ApiError(404, "not_found", f"There is no shop {shop_id!r}.")
        if self.jobs.busy():
            raise ApiError(409, "busy", "Wait for the running task to finish first.")
        with self.shop_lock.write(timeout=SWITCH_WAIT):
            if self.jobs.busy():
                raise ApiError(409, "busy", "Wait for the running task to finish first.")
            if self.shop_id == shop_id:
                self._select("")
            shops.remove(shop_id)
        self.events.publish("shop", {"id": self.shop_id})
        self.set_status_soon(0.0)

    # --- Etsy client ------------------------------------------------------------------------

    def client(self, require_auth: bool = True) -> Any:
        """The shared, thread-safe EtsyClient for the open shop.

        Raises ApiError 409 setup_needed (params.step = "keys" | "connect") when the
        keys are missing or, with require_auth, the shop was never connected. Do not
        close it; do not keep it across a shop switch.
        """
        from .. import auth
        from ..client import EtsyClient

        try:
            config = Config.load()
        except ConfigError as exc:
            raise ApiError(409, "setup_needed", str(exc), step="keys") from exc
        key = (
            self.shop_id,
            auth.token_path().is_file(),  # connecting or disconnecting makes a new client
            config.keystring,
            config.shared_secret,
            config.rate_per_sec,
            config.shop_id,
            tuple(config.scopes),
            config.redirect_uri,
        )
        with self._client_lock:
            if self._client is None or self._client_key != key:
                if self._client is not None:
                    self._retire(self._client)
                self._client = EtsyClient(
                    config, token=auth.load_token(), require_auth=False, transport=self.transport
                )
                self._client_key = key
            client = self._client
        if require_auth and client.token is None:
            raise ApiError(409, "setup_needed", "Connect your Etsy shop first.", step="connect")
        return client

    def reset_client(self) -> None:
        """Forget the shared client, e.g. after new keys, a new sign-in or a disconnect."""
        with self._client_lock:
            if self._client is not None:
                self._retire(self._client)
            self._client = None
            self._client_key = None

    def _retire(self, client: Any) -> None:
        # Another thread may still be mid-request on it: close it later, not now.
        self._retired.append(client)
        timer = threading.Timer(120.0, self._close_client, args=(client,))
        timer.daemon = True
        self._timers.append(timer)
        timer.start()

    def _close_client(self, client: Any) -> None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._retired.remove(client)
        except ValueError:
            pass

    # --- workspace -----------------------------------------------------------------------

    def workspace_root(self) -> Path:
        """The open shop's products folder: the saved one, else the default."""
        from ..drop import workspace as workspace_mod

        saved = str(self.shop_prefs().get("workspace") or "").strip()
        return Path(saved).expanduser() if saved else workspace_mod.default_root()

    def workspace(self, create: bool = True) -> Any:
        """drop.workspace.Workspace for the open shop, created (folders + README) on first use."""
        from ..drop.workspace import Workspace

        ws = Workspace(self.workspace_root())
        key = str(ws.root)
        if create and (key not in self._workspaces_made or not ws.mockups.is_dir()):
            ws.create()
            self._workspaces_made.add(key)
        return ws

    # --- status ----------------------------------------------------------------------------

    @property
    def status(self) -> dict[str, Any]:
        """The last status check, as the browser gets it (names hidden if asked)."""
        return self.public_status()

    def public_status(self) -> dict[str, Any]:
        with self._status_lock:
            status = copy.deepcopy(self._status)
        if status["shop"]["name"] and self.anonymise_names:
            status["shop"]["name"] = self.shop_label(self.shop_index())
        status["shop"]["id"] = self.shop_id
        return status

    def wait_first_status(self, timeout: float) -> bool:
        return self._first_check.wait(timeout)

    def refresh_status(self, force: bool = False) -> dict[str, Any]:
        """Check the shop now, in this thread, and return the new status.

        Without force, a check made less than 10 s ago is reused.
        """
        with self._status_lock:
            fresh = self._status["checked_at"] is not None and (
                time.time() - self._status["checked_at"] < STATUS_DEBOUNCE
            )
        if fresh and not force:
            return self.public_status()
        # Read lock first: a switch holding the write lock may itself re-check.
        with self.shop_lock.read(), self._check_lock:
            with self._status_lock:
                generation = self._status_generation
                previous = copy.deepcopy(self._status)
            try:
                status = self._compute_status()
            except Exception as exc:  # noqa: BLE001 — a status check must never raise
                log.exception("status check failed")
                status = _empty_status(ERROR)
                status["detail"] = str(exc)
                status["checked_at"] = round(time.time(), 3)
            with self._status_lock:
                if generation != self._status_generation:
                    return self.public_status()  # the shop changed meanwhile
                self._status = status
        self._first_check.set()
        if _without_time(previous) != _without_time(status):
            self.events.publish("status", self.public_status())
        return self.public_status()

    def set_status_soon(self, delay: float = 0.3) -> None:
        """Re-check the status in the background shortly (calls within `delay` merge)."""
        if self.closed:
            return
        with self._status_lock:
            if self._soon_timer is not None:
                return
            timer = threading.Timer(delay, self._status_soon)
            timer.daemon = True
            self._soon_timer = timer
        timer.start()

    def _status_soon(self) -> None:
        with self._status_lock:
            self._soon_timer = None
        if self.closed:
            return
        self.refresh_status(force=True)

    def publish_status(self) -> None:
        """Push the current status again (e.g. after the hide-names preference changed)."""
        self.events.publish("status", self.public_status())

    def _compute_status(self) -> dict[str, Any]:
        """The old window's refresh_status, plus the setup facts the pages need."""
        from .. import auth

        status = _empty_status()
        status["checked_at"] = round(time.time(), 3)
        state, detail, shop_info, scopes, quota = self._check_etsy()
        status["state"], status["detail"] = state, detail
        status["scopes"] = scopes
        status["quota_remaining"] = quota
        status["shop"].update(shop_info)
        setup = status["setup"]
        setup["keys"] = state not in (KEYS,)
        setup["connected"] = auth.token_path().is_file()
        setup.update(self._workspace_facts(status["shop"].get("etsy_shop_id")))
        return status

    def _check_etsy(self) -> tuple[str, str, dict[str, Any], list[str], int | None]:
        from .. import auth

        info: dict[str, Any] = {"name": None, "etsy_shop_id": None, "currency": None}
        remembered = shops.current()
        if remembered.name:
            info["name"] = remembered.name
        try:
            Config.load()
        except StallKitError:
            return KEYS, "", info, [], None
        try:
            token = auth.load_token()
        except ConfigError as exc:
            return ERROR, str(exc), info, [], None
        scopes = list(token.scopes) if token else []
        try:
            client = self.client(require_auth=False)
            with client.attempts(2):
                if token is None or client.token is None:
                    client.ping()
                    return DISCONNECTED, "", info, scopes, client.quota_remaining
                shop = client.shop()
        except ApiError as exc:
            return KEYS, exc.message, info, scopes, None
        except EtsyApiError as exc:
            quota = getattr(self._client, "quota_remaining", None)
            if exc.status == 0:
                return OFFLINE, str(exc), info, scopes, quota
            if exc.status == 403 and "api key" in f"{exc.message} {exc.body}".lower():
                return BAD_KEYS, exc.message, info, scopes, quota
            if exc.status == 401:
                return RECONNECT, exc.message, info, scopes, quota
            return ERROR, str(exc), info, scopes, quota
        except AuthUnreachable as exc:
            return OFFLINE, str(exc), info, scopes, None
        except AuthError as exc:
            return RECONNECT, str(exc), info, scopes, None
        except StallKitError as exc:
            return ERROR, str(exc), info, scopes, None
        name = str(shop.get("shop_name") or "")
        shop_id = shop.get("shop_id")
        try:
            shops.remember(name, shop_id)
        except (OSError, StallKitError):
            log.warning("could not remember the shop name")
        info.update(
            name=name or None,
            etsy_shop_id=int(shop_id) if isinstance(shop_id, int) else None,
            currency=shop.get("currency_code") or None,
        )
        if client.token is not None:
            scopes = list(client.token.scopes)
        return CONNECTED, "", info, scopes, client.quota_remaining

    def _workspace_facts(self, etsy_shop_id: int | None) -> dict[str, Any]:
        from ..drop import mockup
        from ..drop.workspace import Workspace

        facts: dict[str, Any] = {}
        try:
            ws = Workspace(self.workspace_root())
            facts["workspace"] = str(ws.root)
            mockups = ws.mockup_files()
            facts["mockups"] = len(mockups)
            try:
                positions = mockup.load_positions(ws.positions_path)
            except StallKitError:
                positions = {}
            names = {p.name for p in mockups}
            facts["mockups_calibrated"] = sum(1 for name in positions if name in names)
            if ws.template_path.is_file():
                facts["template"] = True
                try:
                    data = json.loads(ws.template_path.read_text(encoding="utf-8"))
                    title = data.get("source_title") if isinstance(data, dict) else None
                    facts["template_title"] = str(title) if title else None
                except (OSError, ValueError):
                    facts["template_title"] = None
            facts["designs_pending"] = _pending_designs(ws, etsy_shop_id)
        except OSError:
            log.warning("could not read the workspace for the status")
        return facts

    # --- notifications -------------------------------------------------------------------------

    def _notifications_path(self) -> Path:
        return home_dir() / NOTIFICATIONS_FILE

    def notifications(self) -> list[dict[str, Any]]:
        """The open shop's notifications, newest first."""
        with self._notify_lock:
            return list(reversed(self._read_notifications()))

    def _read_notifications(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._notifications_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [n for n in data if isinstance(n, dict)] if isinstance(data, list) else []

    def _write_notifications(self, items: list[dict[str, Any]]) -> None:
        path = self._notifications_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(items[-KEEP_NOTIFICATIONS:], ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(path)
        except OSError:
            log.warning("could not save notifications")

    def notify(
        self,
        ns: str,
        key: str,
        params: dict[str, Any] | None = None,
        tone: str = "info",
        link: str | None = None,
    ) -> dict[str, Any]:
        """Store a notification for the bell and push it. The UI shows t(ns + ":" + key)."""
        item = {
            "id": uuid.uuid4().hex[:12],
            "ns": ns,
            "key": key,
            "params": dict(params or {}),
            "tone": tone if tone in TONES else "info",
            "link": link,
            "at": round(time.time(), 3),
            "read": False,
        }
        with self._notify_lock:
            items = self._read_notifications()
            items.append(item)
            self._write_notifications(items)
        self.events.publish("notification", item)
        return item

    def mark_notifications_read(self, ids: list[str] | None = None) -> int:
        """Mark some (or all) notifications read; returns how many are still unread."""
        with self._notify_lock:
            items = self._read_notifications()
            for item in items:
                if ids is None or item.get("id") in ids:
                    item["read"] = True
            self._write_notifications(items)
            return sum(1 for item in items if not item.get("read"))

    # --- lifecycle ---------------------------------------------------------------------------

    def request_quit(self) -> None:
        """Ask the launcher to stop the server (after the current response is sent)."""
        callback = self.on_quit
        if callback is None:
            return
        timer = threading.Timer(0.3, callback)
        timer.daemon = True
        timer.start()

    def close(self) -> None:
        """Stop the job worker, end every event stream, close the Etsy clients."""
        self.closed = True
        with self._status_lock:
            if self._soon_timer is not None:
                self._soon_timer.cancel()
                self._soon_timer = None
        self.jobs.stop(timeout=5.0)
        self.events.close()
        with self._client_lock:
            clients = [c for c in [self._client, *self._retired] if c is not None]
            self._client = None
            self._retired = []
            timers, self._timers = self._timers, []
        for timer in timers:
            timer.cancel()
        for client in clients:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


def _without_time(status: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in status.items() if k != "checked_at"}


def _pending_designs(ws: Any, etsy_shop_id: int | None) -> int:
    """Products in 2-PRODUCTS that upload-history.json has not seen for this shop."""
    if not ws.products.is_dir():
        return 0
    groups = ws.product_groups()
    try:
        state = json.loads((ws.root / "upload-history.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    seen: set[str] = set()
    if isinstance(state, dict):
        sections = (
            [state.get(str(etsy_shop_id))] if etsy_shop_id is not None else list(state.values())
        )
        for section in sections:
            if isinstance(section, dict):
                seen.update(name.casefold() for name in section)
    return sum(1 for path, _images in groups if path.name.casefold() not in seen)

