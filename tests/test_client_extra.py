"""The EtsyClient wrappers the web app added, and its thread safety.

Every path, parameter and scope here was checked against Etsy's OpenAPI spec
(https://www.etsy.com/openapi/generated/oas/3.0.0.json). None of these tests
touch the network: requests go to an httpx.MockTransport.
"""

from __future__ import annotations

import threading
import time
import urllib.parse

import httpx
import pytest

from stallkit import auth
from stallkit import client as client_mod
from stallkit.client import EtsyClient, taxonomy_path, walk_taxonomy
from stallkit.config import Config
from stallkit.errors import EtsyApiError

SHOP = 12345678
PREFIX = "/v3/application"


def make_client(handler, *, token: auth.Token | None = None) -> EtsyClient:
    config = Config(keystring="KEY123", shared_secret="SECRET456", shop_id=SHOP, rate_per_sec=10)
    token = token or auth.Token("1.access", "1.refresh", time.time() + 3600, ("transactions_r",))
    return EtsyClient(config, token=token, transport=httpx.MockTransport(handler))


def recorder(responses: dict[str, object]):
    """A handler answering by path (without the /v3/application prefix)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path[len(PREFIX):]
        body = responses.get(path)
        if body is None:
            return httpx.Response(404, json={"error": f"no {path}"})
        if callable(body):
            body = body(request)
        return httpx.Response(200, json=body)

    return handler, seen


def query(request: httpx.Request) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(request.url.query.decode()))


def test_listings_by_shop_sends_state_sort_and_includes():
    handler, seen = recorder({f"/shops/{SHOP}/listings": {"count": 1, "results": [{"listing_id": 1000001}]}})
    client = make_client(handler)
    rows = list(client.listings_by_shop(
        "draft", includes=["Images"], sort_on="updated", sort_order="desc", max_items=5
    ))
    assert rows == [{"listing_id": 1000001}]
    q = query(seen[0])
    assert q["state"] == "draft" and q["sort_on"] == "updated" and q["sort_order"] == "desc"
    assert q["includes"] == "Images"
    assert seen[0].headers["authorization"] == "Bearer 1.access"


def test_listings_by_shop_leaves_unset_sorting_to_etsy():
    handler, seen = recorder({f"/shops/{SHOP}/listings": {"count": 0, "results": []}})
    list(make_client(handler).listings_by_shop())
    q = query(seen[0])
    assert "sort_on" not in q and "sort_order" not in q and q["state"] == "active"


def test_counts_ask_for_one_row_and_read_count():
    handler, seen = recorder({
        f"/shops/{SHOP}/listings": {"count": 42, "results": [{}]},
        f"/shops/{SHOP}/receipts": {"count": 7, "results": [{}]},
    })
    client = make_client(handler)
    assert client.count_listings("active") == 42
    assert client.count_receipts(was_shipped=False, min_created=1700000000) == 7
    listings_q, receipts_q = query(seen[0]), query(seen[1])
    assert listings_q["limit"] == "1" and listings_q["state"] == "active"
    assert receipts_q["limit"] == "1" and receipts_q["was_shipped"] == "false"
    assert receipts_q["min_created"] == "1700000000"


def test_a_count_missing_from_the_answer_is_zero():
    handler, _ = recorder({f"/shops/{SHOP}/listings": {"results": []}})
    assert make_client(handler).count_listings("draft") == 0


def test_listing_images_come_back_in_rank_order():
    handler, seen = recorder({"/listings/1000001/images": {"count": 2, "results": [
        {"listing_image_id": 2, "rank": 2}, {"listing_image_id": 1, "rank": 1},
    ]}})
    images = make_client(handler).listing_images(1000001)
    assert [i["listing_image_id"] for i in images] == [1, 2]
    assert seen[0].method == "GET"


def test_listings_batch_chunks_at_100_ids_and_drops_duplicates():
    def answer(request):
        ids = query(request)["listing_ids"].split(",")
        return {"count": len(ids), "results": [{"listing_id": int(i)} for i in ids]}

    handler, seen = recorder({"/listings/batch": answer})
    ids = list(range(1000001, 1000251)) + [1000001]
    rows = make_client(handler).listings_batch(ids, includes=["Images"])
    assert len(rows) == 250
    assert [len(query(r)["listing_ids"].split(",")) for r in seen] == [100, 100, 50]
    assert all(query(r)["includes"] == "Images" for r in seen)


def test_receipt_and_its_payments():
    handler, seen = recorder({
        f"/shops/{SHOP}/receipts/2000001": {"receipt_id": 2000001},
        f"/shops/{SHOP}/receipts/2000001/payments": {"count": 1, "results": [{"payment_id": 3000001}]},
    })
    client = make_client(handler)
    assert client.receipt(2000001) == {"receipt_id": 2000001}
    assert client.receipt_payments(2000001) == [{"payment_id": 3000001}]


def test_payments_send_ids_comma_separated():
    handler, seen = recorder({f"/shops/{SHOP}/payments": {"count": 1, "results": [{"payment_id": 3000001}]}})
    assert make_client(handler).payments([3000001, 3000002]) == [{"payment_id": 3000001}]
    assert query(seen[0])["payment_ids"] == "3000001,3000002"


def test_payments_of_nothing_asks_nothing():
    handler, seen = recorder({})
    assert make_client(handler).payments([]) == []
    assert seen == []


def test_shop_transactions_paginate():
    pages = {"0": [{"transaction_id": n} for n in range(100)], "100": [{"transaction_id": 100}]}

    def answer(request):
        offset = query(request)["offset"]
        return {"count": 101, "results": pages[offset]}

    handler, seen = recorder({f"/shops/{SHOP}/transactions": answer})
    rows = list(make_client(handler).shop_transactions())
    assert len(rows) == 101 and len(seen) == 2


def test_ledger_entries_need_both_bounds():
    handler, seen = recorder({
        f"/shops/{SHOP}/payment-account/ledger-entries": {"count": 1, "results": [{"entry_id": 1}]}
    })
    rows = list(make_client(handler).ledger_entries(1700000000, 1702592000))
    assert rows == [{"entry_id": 1}]
    q = query(seen[0])
    assert q["min_created"] == "1700000000" and q["max_created"] == "1702592000"


def test_readiness_state_definitions():
    handler, seen = recorder({
        f"/shops/{SHOP}/readiness-state-definitions": {"count": 1, "results": [
            {"readiness_state_id": 5, "readiness_state": "made_to_order",
             "min_processing_days": 1, "max_processing_days": 3},
        ]}
    })
    assert make_client(handler).readiness_state_definitions()[0]["readiness_state_id"] == 5


def test_taxonomy_path_names_a_node_by_its_trail():
    nodes = [{"id": 1, "name": "Home & Living", "children": [
        {"id": 2, "name": "Kitchen", "children": [{"id": 3, "name": "Mugs", "children": []}]},
    ]}]
    assert taxonomy_path(nodes, 3) == "Home & Living > Kitchen > Mugs"
    assert taxonomy_path(nodes, "2") == "Home & Living > Kitchen"
    assert taxonomy_path(nodes, 99) == ""
    assert taxonomy_path(nodes, "not a number") == ""
    assert [node_id for node_id, _ in walk_taxonomy(nodes)] == [1, 2, 3]


# --- thread safety --------------------------------------------------------------


def test_an_expired_token_is_refreshed_once_for_many_threads(monkeypatch):
    refreshes = []

    def refresh(token, config):
        refreshes.append(token.access_token)
        time.sleep(0.05)  # widen the window two threads could both refresh in
        return auth.Token("1.fresh", "1.refresh2", time.time() + 3600, token.scopes)

    monkeypatch.setattr(client_mod.auth, "refresh", refresh)
    handler, seen = recorder({f"/shops/{SHOP}": {"shop_id": SHOP}})
    expired = auth.Token("1.stale", "1.refresh", time.time() - 10, ())
    client = make_client(handler, token=expired)
    threads = [threading.Thread(target=client.shop) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert refreshes == ["1.stale"]
    assert {r.headers["authorization"] for r in seen} == {"Bearer 1.fresh"}


def test_a_401_is_refreshed_once_even_when_several_threads_see_it(monkeypatch):
    refreshes = []

    def refresh(token, config):
        refreshes.append(token.access_token)
        time.sleep(0.05)
        return auth.Token("1.fresh", "1.refresh2", time.time() + 3600, token.scopes)

    monkeypatch.setattr(client_mod.auth, "refresh", refresh)

    def handler(request):
        if request.headers["authorization"] == "Bearer 1.revoked":
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"shop_id": SHOP})

    revoked = auth.Token("1.revoked", "1.refresh", time.time() + 3600, ())
    client = make_client(handler, token=revoked)
    barrier = threading.Barrier(4)

    def call():
        barrier.wait()
        client.shop()

    threads = [threading.Thread(target=call) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert refreshes == ["1.revoked"]


def test_the_attempt_cap_applies_to_the_calling_thread_only(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("down", request=request)

    client = make_client(handler)
    with client.attempts(2), pytest.raises(EtsyApiError) as error:
        client.ping()
    assert error.value.status == 0 and len(calls) == 2
    calls.clear()
    with pytest.raises(EtsyApiError):
        client.ping()
    assert len(calls) == client_mod.MAX_ATTEMPTS


def test_identity_is_looked_up_once_across_threads():
    config = Config(keystring="KEY123", shared_secret="SECRET456", rate_per_sec=10)
    lookups = []

    def handler(request):
        path = request.url.path[len(PREFIX):]
        lookups.append(path)
        if path == "/users/me":
            time.sleep(0.05)
            return httpx.Response(200, json={"user_id": 1})
        if path == "/users/1/shops":
            return httpx.Response(200, json={"shop_id": SHOP})
        return httpx.Response(200, json={"shop_id": SHOP})

    token = auth.Token("1.access", "1.refresh", time.time() + 3600, ())
    client = EtsyClient(config, token=token, transport=httpx.MockTransport(handler))
    threads = [threading.Thread(target=client.shop_id) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert lookups.count("/users/me") == 1 and lookups.count("/users/1/shops") == 1
