"""Starting the app: the port it picks, one server per computer, stopping when idle,
and the self-test the release workflow runs on the packaged app."""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest

import stallkit.web
from stallkit.drop import workspace as workspace_mod
from stallkit.web import launcher
from stallkit.web import server as server_mod
from stallkit.web.context import AppContext
from stallkit.web.server import WebServer


@pytest.fixture(autouse=True)
def off_the_desktop(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(workspace_mod, "desktop_dir", lambda: desktop)


@pytest.fixture
def running():
    """A server with a known token, as a first launch would leave it."""
    ctx = AppContext(token="first-launch-token", check_status=False)
    server = WebServer(ctx, 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.stop()
        thread.join(5)
        server.server_close()
        ctx.close()


# --- ports ------------------------------------------------------------------------------


def test_ports_start_at_3000_and_skip_the_sign_in_listeners():
    assert launcher.PORTS[0] == 3000 and launcher.PORTS[-1] == 0
    assert 3003 not in launcher.PORTS and 8085 not in launcher.PORTS
    assert launcher.candidate_ports(3100)[:2] == [3100, 3000]
    for reserved in (3003, 8085):
        with pytest.raises(ValueError):
            launcher.candidate_ports(reserved)


def test_a_busy_port_falls_through_to_the_next():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    ctx = AppContext(token="t", check_status=False)
    try:
        server = launcher.bind(ctx, ports=[busy, 0])
        try:
            assert server.port not in (busy, 0)
            assert ctx.port == server.port
        finally:
            server.server_close()
    finally:
        blocker.close()
        ctx.close()


def _has_ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
        return True
    except OSError:
        return False


needs_ipv6 = pytest.mark.skipif(not _has_ipv6_loopback(), reason="no IPv6 loopback here")


@needs_ipv6
def test_localhost_is_answered_on_both_loopback_addresses():
    import httpx

    ctx = AppContext(token="t", check_status=False)
    server = launcher.bind(ctx, ports=[0])
    assert server.companion is not None and server.companion.port == server.port
    threads = [threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05},
                                daemon=True) for s in (server, server.companion)]
    for thread in threads:
        thread.start()
    try:
        for host in ("127.0.0.1", "[::1]"):
            with httpx.Client(trust_env=False, verify=False, timeout=5) as http:
                resp = http.get(f"http://{host}:{server.port}/api/ping")
            assert resp.status_code == 200 and resp.json()["app"] == "stallkit", host
    finally:
        server.stop()
        for thread in threads:
            thread.join(5)
        server.server_close()
        ctx.close()
    assert not any(thread.is_alive() for thread in threads)


@needs_ipv6
def test_a_port_someone_else_holds_on_ipv6_counts_as_busy():
    blocker = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    blocker.bind(("::1", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    ctx = AppContext(token="t", check_status=False)
    try:
        server = launcher.bind(ctx, ports=[busy, 0])
        try:
            assert server.port != busy
        finally:
            server.server_close()
    finally:
        blocker.close()
        ctx.close()


def test_stopping_a_server_that_never_served_does_not_hang():
    ctx = AppContext(token="t", check_status=False)
    server = WebServer(ctx, 0)
    server.stop()
    server.serve_forever()  # returns at once: it was stopped first
    server.server_close()
    ctx.close()


def test_the_browser_is_sent_to_localhost_with_the_session_key():
    assert launcher.browser_url(3000, "abc") == "http://localhost:3000/?k=abc"
    assert launcher.browser_url(3001, None, "panel") == "http://localhost:3001/panel"


# --- one server per computer --------------------------------------------------------------


def test_a_running_server_is_found_through_web_json(running):
    launcher.write_state(running.port, "first-launch-token")
    found = launcher.find_running()
    assert found is not None and found["port"] == running.port
    assert found["pid"] == os.getpid()
    assert launcher.ping(running.port, "first-launch-token") is True
    assert launcher.ping(running.port, "some-other-token") is False


def test_a_stale_web_json_is_ignored(running):
    launcher.write_state(running.port, "a-token-from-a-previous-run")
    assert launcher.find_running() is None
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed = probe.getsockname()[1]
    probe.close()
    launcher.write_state(closed, "first-launch-token")
    assert launcher.find_running() is None
    launcher.state_path().write_text("{broken", encoding="utf-8")
    assert launcher.find_running() is None


def test_a_second_launch_opens_the_first_server_instead(running, monkeypatch):
    launcher.write_state(running.port, "first-launch-token")
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    started = []
    monkeypatch.setattr(launcher, "bind", lambda *a, **k: started.append(a))
    assert launcher.launch(open_browser=True) == 0
    assert opened == [f"http://localhost:{running.port}/?k=first-launch-token"]
    assert started == []


def test_web_json_is_only_removed_by_its_own_server():
    launcher.write_state(3000, "mine")
    launcher.clear_state("someone-else")
    assert launcher.state_path().is_file()
    launcher.clear_state("mine")
    assert not launcher.state_path().exists()


# --- a whole launch ----------------------------------------------------------------------------


def test_launch_serves_until_left_idle_then_cleans_up(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    seen = {}

    def ready(server):
        seen["port"] = server.port
        seen["state"] = launcher.state_path().is_file()
        seen["ping"] = launcher.find_running() is not None

    started = time.monotonic()
    code = launcher.launch(open_browser=True, grace=1.0, idle_timeout=1.0, watch_interval=0.05,
                           ports=[0], ready=ready)
    assert code == 0
    assert time.monotonic() - started < 10
    assert seen == {"port": seen["port"], "state": True, "ping": True}
    assert not launcher.state_path().exists()
    deadline = time.monotonic() + 5
    while not opened and time.monotonic() < deadline:
        time.sleep(0.01)
    assert opened and opened[0].startswith(f"http://localhost:{seen['port']}/?k=")
    assert os.environ["STALLKIT_IGNORE_CWD_ENV"] == "1"


def test_quit_from_the_page_ends_the_launch():
    import httpx

    def ready(server):
        def quit_soon():
            with httpx.Client(base_url=f"http://127.0.0.1:{server.port}", trust_env=False,
                              verify=False) as http:
                http.get(f"/?k={server.ctx.token}")
                http.post("/api/quit", json={}, headers={"X-Stallkit": "1"})

        threading.Thread(target=quit_soon, daemon=True).start()

    started = time.monotonic()
    assert launcher.launch(open_browser=False, ports=[0], ready=ready) == 0
    assert time.monotonic() - started < 10


# --- idle watchdog -----------------------------------------------------------------------------


def test_the_watchdog_waits_for_the_first_tab_then_for_the_last_to_close():
    ctx = AppContext(token="t", check_status=False)
    try:
        dog = launcher.IdleWatchdog(ctx, lambda: None, idle_timeout=1.0, grace=3.0)
        now = dog.started
        assert dog.check(now + 2.0) is False  # within the start grace
        assert dog.check(now + 3.5) is True   # nobody ever came

        sub = ctx.events.subscribe()
        assert dog.check(now + 100) is False  # a tab is open
        sub.close()
        closed_at = ctx.events.idle_since
        assert dog.check(closed_at + 0.5) is False
        assert dog.check(closed_at + 1.5) is True

        release = threading.Event()
        job = ctx.jobs.start("test", "common:test", lambda j: release.wait(5))
        deadline = time.monotonic() + 5
        while job.status != "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert dog.check(closed_at + 100) is False  # a job is running
        release.set()
        job.wait(5)
        busy_until = dog.last_busy
        assert dog.check(busy_until + 0.5) is False  # idle time counts from the job's end
        assert dog.check(busy_until + 1.5) is True
    finally:
        ctx.close()


def test_the_watchdog_calls_back_when_idle():
    ctx = AppContext(token="t", check_status=False)
    fired = threading.Event()
    try:
        dog = launcher.IdleWatchdog(ctx, fired.set, idle_timeout=0.1, grace=0.1, interval=0.02)
        dog.start()
        assert fired.wait(5)
    finally:
        ctx.close()


# --- self-test -------------------------------------------------------------------------------------


def make_static(folder, *, uneven=False):
    """A tiny build: the page, two scripts (one with a space in its name), strings."""
    import json

    (folder / "js").mkdir(parents=True)
    (folder / "i18n").mkdir()
    (folder / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (folder / "js" / "app.js").write_text("export {};\n", encoding="utf-8")
    (folder / "js" / "odd name.js").write_text("export {};\n", encoding="utf-8")
    strings = {"tr": {"a": "A"}, "en": {} if uneven else {"a": "A"}}
    (folder / "i18n" / "common.json").write_text(json.dumps(strings), encoding="utf-8")


def test_the_self_test_passes_on_a_complete_build(tmp_path, monkeypatch, capsys):
    static = tmp_path / "static"
    make_static(static)
    monkeypatch.setattr(server_mod, "STATIC_DIR", static)
    home_before = os.environ.get("STALLKIT_HOME")
    assert stallkit.web.self_test() == 0
    assert "self-test passed" in capsys.readouterr().out
    assert os.environ.get("STALLKIT_HOME") == home_before


def test_the_self_test_fails_on_uneven_strings(tmp_path, monkeypatch, capsys):
    static = tmp_path / "static"
    make_static(static, uneven=True)
    monkeypatch.setattr(server_mod, "STATIC_DIR", static)
    assert stallkit.web.self_test() == 1
    assert "common.json" in capsys.readouterr().out


def test_the_self_test_fails_without_the_page(tmp_path, monkeypatch, capsys):
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.js").write_text("export {};\n", encoding="utf-8")
    monkeypatch.setattr(server_mod, "STATIC_DIR", static)
    assert stallkit.web.self_test() == 1
    assert "/ answered 404" in capsys.readouterr().out


def test_the_self_test_gives_up_instead_of_hanging(monkeypatch, capsys):
    monkeypatch.setattr(stallkit.web, "_self_test_steps", lambda: time.sleep(5) or [])
    started = time.monotonic()
    assert stallkit.web.self_test(timeout=0.3) == 1
    assert time.monotonic() - started < 3
    assert "did not finish" in capsys.readouterr().out


@pytest.mark.skipif(not (server_mod.STATIC_DIR / "index.html").is_file(),
                    reason="the UI files are not there yet")
def test_the_self_test_passes_on_the_real_ui(capsys):
    assert stallkit.web.self_test() == 0, capsys.readouterr().out
