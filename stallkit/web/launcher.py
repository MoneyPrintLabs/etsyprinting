"""Start the app: one server per computer, the browser pointed at it, and a quiet exit.

Double-clicking stallkit a second time does not start a second server: the first
one is found through `~/.stallkit/web.json` and a ping, and the browser is simply
pointed at it again. The server stops by itself once no tab has been open (and no
task running) for a while, so closing the browser is all it takes to quit.
"""

from __future__ import annotations

import errno
import logging
import os
import secrets
import sys
import threading
import time
import webbrowser
from datetime import date
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import base_home, read_json, write_json_private
from ..errors import ConfigError

log = logging.getLogger("stallkit.web")

# 3003 is the Etsy sign-in listener and 8085 the Pinterest one: never ours.
PORTS = (3000, 3001, 3002, 3004, 3005, 3006, 3007, 3008, 3009, 3010, 0)
RESERVED_PORTS = frozenset({3003, 8085})
STATE_FILE = "web.json"
PING_TIMEOUT = 1.5
IDLE_TIMEOUT = 90.0
START_GRACE = 180.0
WATCH_INTERVAL = 5.0


def state_path() -> Path:
    return base_home() / STATE_FILE


def browser_url(port: int, token: str | None = None, path: str = "/") -> str:
    """The address the browser opens. Always `localhost`, as the video shows it."""
    path = path if path.startswith("/") else f"/{path}"
    query = f"?k={token}" if token else ""
    return f"http://localhost:{port}{path}{query}"


def instance_of(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def ping(port: int, token: str, timeout: float = PING_TIMEOUT) -> bool:
    """True when our app, started with `token`, answers on `port`."""
    import httpx

    try:
        # Plain http to this machine: no TLS context to build (verify would cost 0.3 s).
        with httpx.Client(trust_env=False, verify=False, timeout=timeout) as http:
            resp = http.get(f"http://127.0.0.1:{port}/api/ping")
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return False
    return (
        resp.status_code == 200
        and isinstance(data, dict)
        and data.get("app") == "stallkit"
        and data.get("instance") == instance_of(token)
    )


def find_running() -> dict[str, Any] | None:
    """The web.json of a server that is up and answering, or None."""
    try:
        data = read_json(state_path())
    except ConfigError:
        return None
    if not isinstance(data, dict):
        return None
    port, token = data.get("port"), data.get("token")
    if not isinstance(port, int) or not isinstance(token, str) or not token:
        return None
    return data if ping(port, token) else None


def write_state(port: int, token: str) -> None:
    write_json_private(
        state_path(), {"pid": os.getpid(), "port": port, "token": token, "version": __version__}
    )


def clear_state(token: str) -> None:
    """Remove web.json, but only if it still describes this server."""
    try:
        data = read_json(state_path())
    except ConfigError:
        data = None
    if isinstance(data, dict) and data.get("token") == token:
        try:
            state_path().unlink()
        except OSError:
            pass


def candidate_ports(port: int | None = None) -> list[int]:
    ports = list(PORTS)
    if port:
        if port in RESERVED_PORTS:
            raise ValueError(f"Port {port} is reserved for signing in to Etsy or Pinterest.")
        ports = [port] + [p for p in ports if p != port]
    return ports


# The address or the whole address family does not exist: no IPv6 on this machine.
_NO_IPV6 = {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT, 10047, 10049}


def bind(
    ctx: Any, port: int | None = None, *, ports: list[int] | None = None, ipv6: bool = True
) -> Any:
    """A WebServer on the first free port of `ports` (default: candidate_ports(port)).

    With ipv6, the same port is also taken on ::1 (`server.companion`): "localhost"
    resolves to ::1 first, so without it a browser waits for that attempt to fail
    on every new connection — and if some other program holds ::1 on a port, the
    browser would reach that program instead of us, so such a port counts as busy.
    Machines without IPv6 get the 127.0.0.1 listener only.
    """
    import socket

    from .server import WebServer

    last: OSError | None = None
    for candidate in ports if ports is not None else candidate_ports(port):
        for _attempt in range(5 if candidate == 0 else 1):
            try:
                server = WebServer(ctx, candidate)
            except OSError as exc:  # in use (EADDRINUSE / WSAEADDRINUSE) or refused: next
                last = exc
                break
            if not ipv6 or not socket.has_ipv6:
                return server
            try:
                server.companion = WebServer(
                    ctx, server.port, family=socket.AF_INET6,
                    router=server.router, static_dir=server.static_dir,
                )
                return server
            except OSError as exc:
                if exc.errno in _NO_IPV6 or getattr(exc, "winerror", None) in _NO_IPV6:
                    return server
                server.server_close()
                last = exc
    raise OSError(f"No free port for stallkit ({last})")


class IdleWatchdog(threading.Thread):
    """Stops the server once nobody is using it.

    Idle means: no event stream open and no job queued or running. The limit is
    `idle_timeout` after the last tab closed, or `grace` after start if no tab
    ever connected (a browser that failed to open should not leave a server behind).
    """

    def __init__(
        self,
        ctx: Any,
        on_idle: Any,
        *,
        idle_timeout: float = IDLE_TIMEOUT,
        grace: float = START_GRACE,
        interval: float = WATCH_INTERVAL,
    ) -> None:
        super().__init__(name="stallkit-idle", daemon=True)
        self.ctx = ctx
        self.on_idle = on_idle
        self.idle_timeout = idle_timeout
        self.grace = grace
        self.interval = interval
        self.started = time.monotonic()
        self.last_busy = 0.0
        self._stop_event = threading.Event()

    def cancel(self) -> None:
        self._stop_event.set()

    def check(self, now: float | None = None) -> bool:
        """One look: True when the server has been idle long enough to stop."""
        now = time.monotonic() if now is None else now
        hub = self.ctx.events
        if hub.client_count:
            return False  # closing the last tab sets hub.idle_since
        if self.ctx.jobs.busy():
            self.last_busy = now  # a job that just ended must not count as idle time
            return False
        if hub.ever_connected:
            since, limit = max(hub.idle_since, self.last_busy), self.idle_timeout
        else:
            since, limit = max(self.started, self.last_busy), self.grace
        return now - since >= limit

    def run(self) -> None:
        while not self._stop_event.wait(self.interval):
            if self.check():
                log.info("no tab open and nothing running: stopping")
                self.on_idle()
                return


def _ensure_streams() -> None:
    """A windowed build has no stdout/stderr: send them to the log file instead."""
    if sys.stdout is None or sys.stderr is None:
        from ..desktop import _log_to_file

        _log_to_file()


def _setup_logging() -> logging.Handler | None:
    """Log to ~/.stallkit/logs/web-<date>.log; returns the handler to remove later."""
    logger = logging.getLogger("stallkit.web")
    try:
        folder = base_home() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(folder / f"web-{date.today().isoformat()}.log",
                                      encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return handler


def _stop_logging(handler: logging.Handler | None) -> None:
    if handler is not None:
        logging.getLogger("stallkit.web").removeHandler(handler)
        handler.close()


def _open_browser(url: str) -> None:
    def run() -> None:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — the printed address is the fallback
            log.warning("could not open the browser")

    threading.Thread(target=run, name="stallkit-browser", daemon=True).start()


def launch(
    *,
    port: int | None = None,
    open_browser: bool = True,
    idle_timeout: float = IDLE_TIMEOUT,
    grace: float = START_GRACE,
    watch_interval: float = WATCH_INTERVAL,
    ports: list[int] | None = None,
    ready: Any = None,
) -> int:
    """Run the app until it is quit or left idle. Returns an exit code.

    The last four parameters exist for tests: shorter idle limits, which ports to
    try, and `ready(server)`, called on its own thread as the server starts serving.
    """
    os.environ["STALLKIT_IGNORE_CWD_ENV"] = "1"
    _ensure_streams()
    handler = _setup_logging()
    try:
        return _run(port=port, open_browser=open_browser, idle_timeout=idle_timeout,
                    grace=grace, watch_interval=watch_interval, ports=ports, ready=ready)
    finally:
        _stop_logging(handler)


def _run(
    *,
    port: int | None,
    open_browser: bool,
    idle_timeout: float,
    grace: float,
    watch_interval: float,
    ports: list[int] | None,
    ready: Any,
) -> int:
    running = find_running()
    if running is not None:
        url = browser_url(running["port"], running["token"])
        print(f"stallkit is already running at http://localhost:{running['port']}/", flush=True)
        if open_browser:
            webbrowser.open(url)
        return 0

    from .context import AppContext

    token = secrets.token_urlsafe(32)
    ctx = AppContext(token=token)
    try:
        server = bind(ctx, port, ports=ports)
    except (OSError, ValueError) as exc:
        ctx.close()
        print(f"stallkit could not start its local server: {exc}", flush=True)
        return 1
    ctx.on_quit = server.stop
    watchdog = IdleWatchdog(
        ctx, server.stop, idle_timeout=idle_timeout, grace=grace, interval=watch_interval
    )
    try:
        write_state(server.port, token)
        print(f"stallkit is running at http://localhost:{server.port}/", flush=True)
        print("Close the browser tab to stop it, or press Ctrl+C here.", flush=True)
        watchdog.start()
        if open_browser:
            _open_browser(browser_url(server.port, token))
        if server.companion is not None:
            threading.Thread(target=server.companion.serve_forever, kwargs={"poll_interval": 0.5},
                             name="stallkit-ipv6", daemon=True).start()
        if ready is not None:
            threading.Thread(target=ready, args=(server,), daemon=True).start()
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
    finally:
        watchdog.cancel()
        if server.companion is not None:
            server.companion.stop_serving()
        server.stopping.set()
        ctx.close()
        server.server_close()
        clear_state(token)
    return 0
