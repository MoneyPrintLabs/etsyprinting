"""Mağaza Bağlantısı: saving the Etsy app keys, connecting, disconnecting.

The consent page itself is Etsy's; here the browser's return trip is played by
an HTTP GET to the real one-shot listener, and the token endpoint is faked.
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.parse

import httpx
import pytest
from web_helpers import (
    ACCESS_TOKEN,
    ETSY_SHOP_ID,
    KEYSTRING,
    REFRESH_TOKEN,
    SHARED_SECRET,
    SHOP_NAME,
    read_events,
    use_fake_etsy,
    wait_for_job,
)

from stallkit import auth
from stallkit.config import DEFAULT_SCOPES
from stallkit.desktop import settings
from stallkit.web.api import connect


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _local_callback(port: int) -> str:
    return f"http://localhost:{port}/oauth/redirect"


@pytest.fixture
def token_endpoint(monkeypatch):
    """Etsy's token endpoint: grants what `scope` says (all default scopes unless changed)."""
    grant = {"scope": " ".join(DEFAULT_SCOPES)}
    calls = []

    def post_token(form):
        calls.append(dict(form))
        return {"access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN, "expires_in": 3600,
                "token_type": "Bearer", "scope": grant["scope"]}

    monkeypatch.setattr(auth, "_post_token", post_token)
    return {"grant": grant, "calls": calls}


def _start(web, **body):
    resp = web.client.post("/api/connect/start", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _come_back(url: str, port: int, **params) -> httpx.Response:
    """What the browser does after the person answers Etsy's page."""
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    query = {"state": state, **params}
    with httpx.Client(trust_env=False) as http:
        return http.get(f"http://127.0.0.1:{port}/oauth/redirect", params=query,
                        follow_redirects=False)


# --- info --------------------------------------------------------------------------------


def test_info_before_anything_is_set_up(web):
    data = web.client.get("/api/connect/info").json()
    assert data["keys"] is False and data["connected"] is False
    assert data["keystring_prefix"] == "" and data["secret_length"] == 0
    assert data["redirect_uri"] == settings.ETSY_REDIRECT_DEFAULT and data["redirect_saved"] is False
    assert data["redirect_ok"] is True and data["redirect_local"] is True
    assert data["scopes_requested"] == list(DEFAULT_SCOPES)
    assert data["scopes_granted"] == [] and data["missing_scopes"] == []
    assert data["job"] is None and data["shop_name"] is None
    assert set(DEFAULT_SCOPES) <= set(data["known_scopes"])


def test_info_never_contains_the_secret_and_reports_missing_scopes(web):
    use_fake_etsy(web)
    auth.save_token(auth.Token(ACCESS_TOKEN, REFRESH_TOKEN, time.time() + 3600,
                               scopes=("shops_r", "listings_r")))
    resp = web.client.get("/api/connect/info")
    assert SHARED_SECRET not in resp.text
    data = resp.json()
    assert data["keys"] is True and data["connected"] is True
    assert data["keystring_prefix"] == KEYSTRING[:6] and data["secret_length"] == len(SHARED_SECRET)
    assert data["scopes_granted"] == ["shops_r", "listings_r"]
    assert data["missing_scopes"] == ["listings_w", "transactions_r", "transactions_w"]


# --- keys --------------------------------------------------------------------------------------


def test_saving_keys_checks_them_with_etsy_and_hides_the_secret(web):
    fake = use_fake_etsy(web, keys=False, connected=False)
    resp = web.client.post("/api/connect/keys",
                           json={"keystring": f" {KEYSTRING} ", "shared_secret": SHARED_SECRET})
    assert resp.status_code == 200, resp.text
    assert SHARED_SECRET not in resp.text
    data = resp.json()
    assert data["check"] == "ok" and data["saved"] is True and data["token_cleared"] is False
    assert data["keystring_prefix"] == KEYSTRING[:6] and data["secret_length"] == len(SHARED_SECRET)
    assert data["redirect_uri"] == settings.ETSY_REDIRECT_DEFAULT
    assert data["status"]["state"] == "disconnected"
    assert ("GET", "/openapi-ping") in fake.calls
    assert fake.requests[0].headers["x-api-key"] == f"{KEYSTRING}:{SHARED_SECRET}"
    env = settings.env_path().read_text(encoding="utf-8")
    assert f"ETSY_KEYSTRING={KEYSTRING}" in env and f"ETSY_SHARED_SECRET={SHARED_SECRET}" in env
    assert f"ETSY_REDIRECT_URI={settings.ETSY_REDIRECT_DEFAULT}" in env


def test_the_colon_joined_credential_is_accepted_in_one_field(web):
    use_fake_etsy(web, keys=False, connected=False)
    data = web.client.post("/api/connect/keys",
                           json={"keystring": f"{KEYSTRING}:{SHARED_SECRET}"}).json()
    assert data["check"] == "ok" and data["secret_length"] == len(SHARED_SECRET)
    assert settings.current("ETSY_KEYSTRING") == KEYSTRING
    assert settings.current("ETSY_SHARED_SECRET") == SHARED_SECRET


def test_keys_etsy_refuses_are_saved_but_reported(web):
    fake = use_fake_etsy(web, keys=False, connected=False)
    fake.error("GET", "/openapi-ping", 403, "Invalid API key: should be in the format 'keystring:shared_secret'")
    data = web.client.post("/api/connect/keys",
                           json={"keystring": KEYSTRING, "shared_secret": "WRONG"}).json()
    assert data["check"] == "rejected" and "Invalid API key" in data["reason"]
    assert data["status"]["state"] == "bad_keys"


def test_keys_cannot_be_checked_offline(web):
    fake = use_fake_etsy(web, keys=False, connected=False)
    fake.offline = True
    data = web.client.post("/api/connect/keys",
                           json={"keystring": KEYSTRING, "shared_secret": SHARED_SECRET}).json()
    assert data["check"] == "offline" and data["status"]["state"] == "offline"


@pytest.mark.parametrize("body, field", [
    ({}, "keystring"),
    ({"keystring": KEYSTRING}, "shared_secret"),
    ({"shared_secret": SHARED_SECRET}, "keystring"),
])
def test_both_halves_are_needed(web, body, field):
    resp = web.client.post("/api/connect/keys", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "need_both_keys"
    assert resp.json()["error"]["params"]["field"] == field


def test_a_key_with_spaces_inside_is_refused(web):
    resp = web.client.post("/api/connect/keys", json={"keystring": "KEY 123", "shared_secret": "x"})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "invalid_key"


def test_an_ip_callback_is_refused(web):
    resp = web.client.post("/api/connect/keys", json={
        "keystring": KEYSTRING, "shared_secret": SHARED_SECRET,
        "redirect_uri": "http://127.0.0.1:3003/oauth/redirect"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "bad_redirect"
    assert not settings.env_path().exists()


def test_empty_fields_keep_the_saved_keys(web):
    use_fake_etsy(web, connected=False)
    data = web.client.post("/api/connect/keys",
                           json={"redirect_uri": "http://localhost:3005/etsy"}).json()
    assert data["check"] == "ok" and data["redirect_uri"] == "http://localhost:3005/etsy"
    assert settings.current("ETSY_KEYSTRING") == KEYSTRING
    assert settings.current("ETSY_SHARED_SECRET") == SHARED_SECRET


def test_a_new_keystring_needs_its_own_secret(web):
    use_fake_etsy(web, connected=False)
    resp = web.client.post("/api/connect/keys", json={"keystring": "OTHERKEY9"})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "need_both_keys"


def test_keys_of_another_app_remove_the_old_sign_in(web):
    use_fake_etsy(web)
    assert auth.token_path().is_file()
    same = web.client.post("/api/connect/keys", json={"keystring": KEYSTRING}).json()
    assert same["token_cleared"] is False and auth.token_path().is_file()
    other = web.client.post("/api/connect/keys",
                            json={"keystring": "OTHERKEY9", "shared_secret": "OTHERSECRET"}).json()
    assert other["token_cleared"] is True and not auth.token_path().exists()
    assert other["status"]["state"] == "disconnected"


def test_keys_are_not_changed_while_a_task_runs(web):
    release = threading.Event()
    job = web.ctx.jobs.start("other", "x:y", lambda job: release.wait(5))
    try:
        resp = web.client.post("/api/connect/keys",
                               json={"keystring": KEYSTRING, "shared_secret": SHARED_SECRET})
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "busy"
    finally:
        release.set()
        job.wait(5)


# --- connect: refusals ---------------------------------------------------------------------------


def test_connecting_needs_the_keys_first(web):
    resp = web.client.post("/api/connect/start", json={})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "setup_needed"
    assert resp.json()["error"]["params"]["step"] == "keys"


def test_a_callback_this_computer_cannot_catch_is_explained(web):
    use_fake_etsy(web, connected=False)
    settings.save({"ETSY_REDIRECT_URI": "https://example.com/etsy-callback"})
    resp = web.client.post("/api/connect/start", json={})
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "callback_not_local"
    assert error["params"] == {"redirect_uri": "https://example.com/etsy-callback",
                               "suggested": settings.ETSY_REDIRECT_DEFAULT}


def test_an_unknown_extra_scope_is_refused(web):
    use_fake_etsy(web, connected=False)
    resp = web.client.post("/api/connect/start", json={"extra_scopes": ["everything_w"]})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "unknown_scope"


def test_a_busy_callback_port_is_reported(web):
    use_fake_etsy(web, connected=False)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    settings.save({"ETSY_REDIRECT_URI": _local_callback(port)})
    try:
        resp = web.client.post("/api/connect/start", json={})
    finally:
        blocker.close()
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "port_in_use"
    assert resp.json()["error"]["params"]["port"] == port


def test_connecting_waits_for_other_tasks(web):
    use_fake_etsy(web, connected=False)
    release = threading.Event()
    job = web.ctx.jobs.start("designs", "x:y", lambda job: release.wait(5))
    try:
        resp = web.client.post("/api/connect/start", json={})
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "busy"
    finally:
        release.set()
        job.wait(5)


# --- connect: the whole trip ----------------------------------------------------------------


def test_connecting_end_to_end(web, token_endpoint):
    fake = use_fake_etsy(web, connected=False)
    port = _free_port()
    settings.save({"ETSY_REDIRECT_URI": _local_callback(port)})
    started = _start(web)
    assert started["url"].startswith("https://www.etsy.com/oauth/connect?")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(started["url"]).query)
    assert query["redirect_uri"] == [_local_callback(port)]
    assert query["client_id"] == [KEYSTRING] and query["code_challenge_method"] == ["S256"]
    assert query["scope"][0].split() == list(DEFAULT_SCOPES)
    # The listener is up before the answer arrives: the browser can go to Etsy at once.
    assert not auth.port_is_free(port)
    info = web.client.get("/api/connect/info").json()
    assert info["job"]["id"] == started["job_id"] and info["job"]["state"]["phase"] == "opened"

    back = _come_back(started["url"], port, code="the-code")
    assert back.status_code == 302
    assert back.headers["location"] == f"http://localhost:{web.port}/oauth-done"
    assert "the-code" not in back.headers["location"]

    job = wait_for_job(web, started["job_id"])
    assert job["status"] == "done", job
    assert job["result"] == {"shop_name": SHOP_NAME, "scopes": list(DEFAULT_SCOPES),
                             "missing_scopes": []}
    assert job["state"]["phase"] == "done"
    assert token_endpoint["calls"][0]["code"] == "the-code"
    assert token_endpoint["calls"][0]["redirect_uri"] == _local_callback(port)
    assert auth.load_token().access_token == ACCESS_TOKEN
    assert ("GET", f"/shops/{ETSY_SHOP_ID}") in fake.calls
    assert web.client.get("/api/status").json()["state"] == "connected"
    assert auth._CallbackHandler.return_url is None
    assert auth.port_is_free(port)
    notes = web.client.get("/api/notifications").json()["items"]
    assert notes[0]["ns"] == "connect" and notes[0]["params"] == {"shop": SHOP_NAME}


def test_the_connect_job_reports_its_phases(web, token_endpoint):
    use_fake_etsy(web, connected=False)
    port = _free_port()
    settings.save({"ETSY_REDIRECT_URI": _local_callback(port)})
    box = {}

    def begin():
        box.update(_start(web))
        threading.Timer(0.2, lambda: _come_back(box["url"], port, code="c")).start()

    events = read_events(
        web,
        lambda evs: any(t == "job" and d.get("kind") == "connect" and d["status"] == "done"
                        for t, d in evs),
        after_connect=begin,
    )
    phases = [d["data"]["phase"] for t, d in events
              if t == "job-event" and d["kind"] == "connect" and d["type"] == "phase"]
    assert phases == ["opened", "code_received", "fetching_shop", "done"]


def test_extra_scopes_are_asked_for_and_kept(web, token_endpoint):
    use_fake_etsy(web, connected=False)
    port = _free_port()
    settings.save({"ETSY_REDIRECT_URI": _local_callback(port)})
    token_endpoint["grant"]["scope"] = "shops_r listings_r"
    started = _start(web, extra_scopes=["email_r"])
    scope = urllib.parse.parse_qs(urllib.parse.urlparse(started["url"]).query)["scope"][0]
    assert scope.split() == [*DEFAULT_SCOPES, "email_r"]
    assert settings.current("ETSY_SCOPES").split() == [*DEFAULT_SCOPES, "email_r"]
    _come_back(started["url"], port, code="c")
    job = wait_for_job(web, started["job_id"])
    assert job["result"]["missing_scopes"] == ["listings_w", "transactions_r", "transactions_w",
                                               "email_r"]


def test_connecting_can_be_cancelled(web):
    use_fake_etsy(web, connected=False)
    port = _free_port()
    settings.save({"ETSY_REDIRECT_URI": _local_callback(port)})
    started = _start(web)
    web.client.post(f"/api/jobs/{started['job_id']}/cancel")
    job = wait_for_job(web, started["job_id"])
    assert job["status"] == "cancelled" and job["error"] is None
    assert auth.port_is_free(port)
    assert not auth.token_path().exists()


def test_a_second_connect_replaces_the_first(web):
    use_fake_etsy(web, connected=False)
    port = _free_port()
    settings.save({"ETSY_REDIRECT_URI": _local_callback(port)})
    first = _start(web)
    second = _start(web)
    try:
        assert first["job_id"] != second["job_id"]
        assert web.client.get(f"/api/jobs/{first['job_id']}").json()["status"] == "cancelled"
        assert web.client.get(f"/api/jobs/{second['job_id']}").json()["status"] == "running"
    finally:
        web.client.post(f"/api/jobs/{second['job_id']}/cancel")
        wait_for_job(web, second["job_id"])


def test_saying_no_on_etsy_is_reported(web):
    use_fake_etsy(web, connected=False)
    port = _free_port()
    settings.save({"ETSY_REDIRECT_URI": _local_callback(port)})
    started = _start(web)
    back = _come_back(started["url"], port, error="access_denied",
                      error_description="The user denied the request")
    assert back.status_code == 400  # the listener's own page; nothing to go back to
    job = wait_for_job(web, started["job_id"])
    assert job["status"] == "error"
    assert job["error"]["code"] == "consent_denied"
    assert job["error"]["params"] == {"etsy_error": "access_denied"}


def test_an_answer_to_another_request_is_refused(web, token_endpoint):
    use_fake_etsy(web, connected=False)
    port = _free_port()
    settings.save({"ETSY_REDIRECT_URI": _local_callback(port)})
    started = _start(web)
    with httpx.Client(trust_env=False) as http:
        http.get(f"http://127.0.0.1:{port}/oauth/redirect",
                 params={"code": "c", "state": "forged"}, follow_redirects=False)
    job = wait_for_job(web, started["job_id"])
    assert job["error"]["code"] == "state_mismatch"
    assert token_endpoint["calls"] == [] and not auth.token_path().exists()


def test_waiting_too_long_times_out(web, monkeypatch):
    use_fake_etsy(web, connected=False)
    port = _free_port()
    settings.save({"ETSY_REDIRECT_URI": _local_callback(port)})
    monkeypatch.setattr(connect, "CONNECT_TIMEOUT", 2.5)
    started = _start(web)
    job = wait_for_job(web, started["job_id"], timeout=15)
    assert job["error"]["code"] == "connect_timeout"


def test_a_refused_code_exchange_is_reported(web, monkeypatch):
    use_fake_etsy(web, connected=False)
    port = _free_port()
    settings.save({"ETSY_REDIRECT_URI": _local_callback(port)})

    def refuse(form):
        raise auth.AuthError('Token endpoint rejected the request (400): {"error":"invalid_grant"}')

    monkeypatch.setattr(auth, "_post_token", refuse)
    started = _start(web)
    _come_back(started["url"], port, code="c")
    job = wait_for_job(web, started["job_id"])
    assert job["error"]["code"] == "token_refused" and "invalid_grant" in job["error"]["message"]


def test_a_missing_callback_gets_the_default(web, monkeypatch):
    use_fake_etsy(web, connected=False)
    settings.save({"ETSY_REDIRECT_URI": ""})
    monkeypatch.setattr(auth, "port_is_free", lambda port: False)
    resp = web.client.post("/api/connect/start", json={})
    assert resp.json()["error"]["code"] == "port_in_use"  # stopped before listening
    assert settings.current("ETSY_REDIRECT_URI") == settings.ETSY_REDIRECT_DEFAULT


# --- disconnect ------------------------------------------------------------------------------------


def test_disconnecting_forgets_the_sign_in_but_keeps_the_keys(web):
    use_fake_etsy(web)
    resp = web.client.post("/api/connect/disconnect", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["removed"] is True and data["status"]["state"] == "disconnected"
    assert not auth.token_path().exists()
    assert settings.current("ETSY_KEYSTRING") == KEYSTRING
    again = web.client.post("/api/connect/disconnect", json={}).json()
    assert again["removed"] is False


def test_disconnecting_waits_for_tasks(web):
    use_fake_etsy(web)
    release = threading.Event()
    job = web.ctx.jobs.start("orders", "x:y", lambda job: release.wait(5))
    try:
        resp = web.client.post("/api/connect/disconnect", json={})
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "busy"
        assert auth.token_path().is_file()
    finally:
        release.set()
        job.wait(5)


def test_the_oauth_done_page_is_the_app(web):
    with web.anonymous() as http:  # Etsy's redirect chain carries no SameSite=Strict cookie
        resp = http.get("/oauth-done")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
