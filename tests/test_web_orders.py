"""Siparişler: orders list, tabs, thumbnails, carriers, tracking upload job, CSV.

A real server on a free port (the `web` fixture) with a fake Etsy. Every value is
invented: shop 12345678, receipts 3000001..., listings 1000001...
"""

from __future__ import annotations

import csv
import io
import json
import threading

import httpx
import pytest
from web_helpers import ETSY_SHOP_ID, read_events, use_fake_etsy, wait_for_job

from stallkit.client import EtsyClient
from stallkit.config import base_home
from stallkit.web.api import orders as orders_api

RECEIPTS = f"/shops/{ETSY_SHOP_ID}/receipts"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(EtsyClient, "_backoff", staticmethod(lambda attempt: 0.0))


def money(cents: int) -> dict:
    return {"amount": cents, "divisor": 100, "currency_code": "USD"}


def make_receipt(n: int, *, shipped: bool = False, delivered: bool = False,
                 status: str = "paid", items: int = 1) -> dict:
    receipt_id = 3000000 + n
    transactions = []
    for k in range(items):
        transactions.append({
            "transaction_id": 5000000 + n * 10 + k,
            "title": f"Example Poster {n}-{k}, Wall Art, Gift",
            "quantity": 1 + k,
            "listing_id": 1000000 + n,
            "listing_image_id": 7000000 + n,
            "variations": [
                {"property_id": 1, "value_id": 2, "formatted_name": "Size", "formatted_value": "A3"},
                {"property_id": 3, "value_id": 4, "formatted_name": "Color", "formatted_value": "Black"},
            ],
        })
    return {
        "receipt_id": receipt_id,
        "name": f"Example Buyer{n}",
        "buyer_email": f"buyer{n}@example.com",
        "first_line": "1 Example Street",
        "city": "Exampleton",
        "state": "EX",
        "zip": "00000",
        "country_iso": "US",
        "status": status,
        "is_paid": True,
        "is_shipped": shipped or delivered,
        "created_timestamp": 1790000000 - n * 3600,
        "grandtotal": money(2980 + n),
        "total_price": money(2490),
        "shipments": (
            [{"receipt_shipping_id": 1, "carrier_name": "UPS", "tracking_code": f"1ZEXAMPLE{n}",
              "shipment_notification_timestamp": 1790000000}]
            if shipped or delivered else []
        ),
        "transactions": transactions,
        "_delivered": delivered,
    }


class ReceiptStore:
    """Answers getShopReceipts like Etsy: filters, limit/offset, count."""

    def __init__(self, receipts: list[dict]) -> None:
        self.receipts = receipts
        self.queries: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        self.queries.append(q)
        rows = list(self.receipts)

        def flag(name: str) -> bool | None:
            return None if name not in q else q[name] == "true"

        if flag("was_shipped") is not None:
            rows = [r for r in rows if r["is_shipped"] == flag("was_shipped")]
        if flag("was_delivered") is not None:
            rows = [r for r in rows if r["_delivered"] == flag("was_delivered")]
        if flag("was_canceled") is not None:
            rows = [r for r in rows if (r["status"] == "canceled") == flag("was_canceled")]
        if "min_created" in q:
            rows = [r for r in rows if r["created_timestamp"] >= int(q["min_created"])]
        if "min_last_modified" in q:
            since = int(q["min_last_modified"])
            rows = [r for r in rows if r.get("updated_timestamp", r["created_timestamp"]) >= since]
        offset, limit = int(q.get("offset", 0)), int(q.get("limit", 25))
        page = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
        return httpx.Response(200, json={"count": len(rows), "results": page[offset:offset + limit]})


def setup_orders(web, receipts: list[dict], *, listings: list[dict] | None = None):
    fake = use_fake_etsy(web)
    store = ReceiptStore(receipts)
    fake.add("GET", RECEIPTS, store)
    fake.add("GET", "/listings/batch", {"count": len(listings or []), "results": listings or []})
    fake.add("GET", "/shipping-carriers", {"count": 3, "results": [
        {"shipping_carrier_id": 1, "name": "USPS"},
        {"shipping_carrier_id": 2, "name": "UPS"},
        {"shipping_carrier_id": 3, "name": "DHL"},
    ]})
    fake.add("GET", f"/shops/{ETSY_SHOP_ID}/shipping-profiles", {"count": 1, "results": [
        {"shipping_profile_id": 1, "origin_country_iso": "TR", "is_deleted": False},
    ]})
    return fake, store


# --- setup and errors ------------------------------------------------------------------


def test_needs_setup_without_keys(web):
    resp = web.client.get("/api/orders")
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "setup_needed" and error["params"] == {"step": "keys"}


def test_needs_a_connection_with_keys_only(web):
    use_fake_etsy(web, connected=False)
    resp = web.client.get("/api/orders/summary")
    assert resp.status_code == 409
    assert resp.json()["error"]["params"] == {"step": "connect"}


def test_offline(web):
    fake, _store = setup_orders(web, [])
    fake.offline = True
    resp = web.client.get("/api/orders", params={"tab": "shipped"})
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "offline"


def test_bad_tab_and_paging(web):
    setup_orders(web, [])
    assert web.client.get("/api/orders", params={"tab": "nope"}).json()["error"]["code"] == "invalid"
    assert web.client.get("/api/orders", params={"per_page": "500"}).status_code == 422


# --- listing -------------------------------------------------------------------------------


@pytest.mark.parametrize("tab, expected", [
    ("unshipped", {"was_paid": "true", "was_shipped": "false", "was_canceled": "false"}),
    ("shipped", {"was_paid": "true", "was_shipped": "true", "was_delivered": "false",
                 "was_canceled": "false"}),
    ("delivered", {"was_paid": "true", "was_delivered": "true"}),
    ("all", {"was_paid": "true"}),
])
def test_tab_filters(web, tab, expected):
    _fake, store = setup_orders(web, [make_receipt(1)])
    resp = web.client.get("/api/orders", params={"tab": tab})
    assert resp.status_code == 200, resp.text
    sent = store.queries[0]
    for key in ("was_paid", "was_shipped", "was_delivered", "was_canceled"):
        assert sent.get(key) == expected.get(key), (tab, key)
    assert sent["sort_on"] == "created" and sent["sort_order"] == "desc"


def test_rows_are_flattened_newest_first_without_email(web):
    receipts = [make_receipt(1), make_receipt(2, items=3), make_receipt(3, shipped=True)]
    setup_orders(web, receipts)
    data = web.client.get("/api/orders", params={"tab": "all", "per_page": 2}).json()
    assert data["total"] == 3 and data["pages"] == 2 and data["page"] == 1
    first, second = data["rows"]
    assert first["receipt_id"] == 3000001 and second["receipt_id"] == 3000002
    assert first["buyer"] == "Example B."
    assert (first["city"], first["state"], first["country"]) == ("Exampleton", "EX", "US")
    assert first["total"] == money(2981)
    assert first["status"] == "unshipped" and first["shipments"] == []
    item = first["items"][0]
    assert item["title"].startswith("Example Poster 1-0")
    assert item["variations"] == [{"name": "Size", "value": "A3"}, {"name": "Color", "value": "Black"}]
    assert second["item_count"] == 1 + 2 + 3 and len(second["items"]) == 3
    text = json.dumps(data)
    assert "@example.com" not in text and "Example Street" not in text
    page2 = web.client.get("/api/orders", params={"tab": "all", "per_page": 2, "page": 2}).json()
    assert [r["receipt_id"] for r in page2["rows"]] == [3000003]
    assert page2["rows"][0]["status"] == "shipped"
    assert page2["rows"][0]["shipments"][0]["tracking_code"] == "1ZEXAMPLE3"


def test_delivered_and_canceled_status():
    assert orders_api.receipt_status({"is_shipped": True}, "delivered") == "delivered"
    assert orders_api.receipt_status({"is_shipped": True}, "all") == "shipped"
    assert orders_api.receipt_status({"is_shipped": False, "status": "Canceled"}) == "canceled"
    assert orders_api.receipt_status({"is_shipped": False, "status": "paid"}) == "unshipped"
    assert orders_api.short_name("Ayşe Nur yılmaz") == "Ayşe Y."
    assert orders_api.short_name("Cher") == "Cher" and orders_api.short_name(None) == ""


def test_thumbnails_by_listing_image(web):
    receipts = [make_receipt(1), make_receipt(2), make_receipt(3)]
    listings = [
        {"listing_id": 1000001, "images": [
            {"listing_image_id": 1, "rank": 1, "url_75x75": "https://i.example.com/1-first.jpg"},
            {"listing_image_id": 7000001, "rank": 2, "url_75x75": "https://i.example.com/1-own.jpg"},
        ]},
        {"listing_id": 1000002, "images": [
            {"listing_image_id": 9, "rank": 1, "url_75x75": "https://i.example.com/2-first.jpg"},
        ]},
        # 1000003 is gone from Etsy: no thumbnail, no error.
    ]
    fake, _store = setup_orders(web, receipts, listings=listings)
    rows = web.client.get("/api/orders", params={"tab": "all"}).json()["rows"]
    thumbs = [r["items"][0]["thumb"] for r in rows]
    assert thumbs == ["https://i.example.com/1-own.jpg", "https://i.example.com/2-first.jpg", None]
    batch = [r for r in fake.requests if r.url.path.endswith("/listings/batch")]
    assert len(batch) == 1
    assert batch[0].url.params["includes"] == "Images"
    assert sorted(batch[0].url.params["listing_ids"].split(",")) == ["1000001", "1000002", "1000003"]
    web.client.get("/api/orders", params={"tab": "all"})  # cached: no second batch call
    assert len([r for r in fake.requests if r.url.path.endswith("/listings/batch")]) == 1


def test_search_by_number_name_and_title(web):
    receipts = [make_receipt(n) for n in range(1, 6)]
    receipts[2]["name"] = "Zeynep Kaya"
    setup_orders(web, receipts)
    get = lambda q: [r["receipt_id"] for r in web.client.get(  # noqa: E731
        "/api/orders", params={"tab": "unshipped", "q": q}).json()["rows"]]
    assert get("#3000004") == [3000004]
    assert get("zeynep") == [3000003]
    assert get("poster 5-0") == [3000005]
    assert get("nothing like this") == []
    whole = web.client.get("/api/orders", params={"tab": "unshipped", "per_page": 2}).json()
    assert whole["ids"] == [3000001, 3000002, 3000003, 3000004, 3000005]
    assert [r["receipt_id"] for r in whole["rows"]] == [3000001, 3000002]
    assert web.client.get("/api/orders", params={"tab": "shipped"}).json()["ids"] is None


def test_full_order_number_from_another_tab_is_looked_up(web):
    fake, _store = setup_orders(web, [make_receipt(1)])
    other = make_receipt(9, shipped=True)
    other.pop("_delivered")
    fake.add("GET", f"{RECEIPTS}/3000009", other)
    rows = web.client.get("/api/orders", params={"tab": "unshipped", "q": "3000009"}).json()["rows"]
    assert [r["receipt_id"] for r in rows] == [3000009] and rows[0]["status"] == "shipped"


def test_anonymise_hides_buyer_names(web):
    receipts = [make_receipt(1), make_receipt(2)]
    receipts[0]["name"] = "Hidden Person"
    setup_orders(web, receipts)
    web.client.post("/api/prefs", json={"anonymise": True, "language": "tr"})
    data = web.client.get("/api/orders", params={"tab": "unshipped"}).json()
    assert [r["buyer"] for r in data["rows"]] == ["Alıcı 1", "Alıcı 2"]
    assert "Hidden" not in json.dumps(data)
    # A hidden name cannot be found by searching for it either.
    assert web.client.get("/api/orders", params={"q": "hidden"}).json()["rows"] == []


def test_summary_counts(web):
    receipts = [make_receipt(1), make_receipt(2), make_receipt(3, shipped=True),
                make_receipt(4, delivered=True), make_receipt(5, status="canceled")]
    setup_orders(web, receipts)
    data = web.client.get("/api/orders/summary").json()
    assert data["counts"] == {"unshipped": 2, "shipped": 1, "delivered": 1, "all": 5}
    assert data["tracking_restricted"] is False
    assert data["sold_orders_url"] == "https://www.etsy.com/your/orders/sold"


# --- carriers ------------------------------------------------------------------------------


def test_carriers_default_country_from_shipping_profile_and_cache(web):
    fake, _store = setup_orders(web, [])
    data = web.client.get("/api/orders/carriers").json()
    assert data["country"] == "TR" and data["source"] == "profile"
    assert [c["name"] for c in data["carriers"]] == ["DHL", "UPS", "USPS"]
    assert data["other"] == "other" and data["last_carrier"] == ""
    carrier_calls = [r for r in fake.requests if r.url.path.endswith("/shipping-carriers")]
    assert carrier_calls[0].url.params["origin_country_iso"] == "TR"
    web.client.get("/api/orders/carriers")  # a day-long cache on disk
    assert len([r for r in fake.requests if r.url.path.endswith("/shipping-carriers")]) == 1
    assert "TR" in json.loads((base_home() / "cache" / "shipping-carriers.json").read_text("utf-8"))


def test_set_country_is_saved_per_shop(web):
    setup_orders(web, [])
    resp = web.client.post("/api/orders/country", json={"country": "us"})
    assert resp.status_code == 200 and resp.json()["country"] == "US"
    assert web.ctx.shop_prefs()["country"] == "US"
    assert web.client.get("/api/orders/carriers").json()["source"] == "pref"
    bad = web.client.post("/api/orders/country", json={"country": "Turkey"})
    assert bad.status_code == 422 and bad.json()["error"]["params"]["field"] == "country"


# --- shipping --------------------------------------------------------------------------------


def shipment_answer(request: httpx.Request) -> dict:
    body = json.loads(request.content)
    receipt_id = int(request.url.path.split("/")[-2])
    return {"receipt_id": receipt_id, "is_shipped": True, "shipments": [
        {"carrier_name": body["carrier_name"], "tracking_code": body["tracking_code"],
         "shipment_notification_timestamp": 1790000000}]}


def test_ship_job_sends_and_reports_each_row(web):
    receipts = [make_receipt(1), make_receipt(2), make_receipt(3, shipped=True)]
    fake, _store = setup_orders(web, receipts)
    for n in (1, 2, 3):
        fake.add("POST", f"{RECEIPTS}/{3000000 + n}/tracking", shipment_answer)
    rows = [
        {"receipt_id": 3000001, "carrier_name": "ups", "tracking_code": "1ZEXAMPLE01"},
        {"receipt_id": "#3000002", "carrier_name": "USPS", "tracking_code": "9400EXAMPLE02",
         "note_to_buyer": "Thanks"},
        {"receipt_id": 3000003, "carrier_name": "UPS", "tracking_code": "1ZEXAMPLE03"},
    ]

    def done(events):
        return any(t == "job" and d["kind"] == "orders" and d["status"] == "done" for t, d in events)

    started = {}
    events = read_events(web, done, after_connect=lambda: started.update(
        job=web.client.post("/api/orders/ship",
                            json={"rows": rows, "country": "TR", "confirm": True}).json()))
    job = wait_for_job(web, started["job"]["id"])
    assert job["title_key"] == "orders:job.ship" and job["params"] == {"n": 3}
    result = job["result"]
    assert (result["sent"], result["failed"], result["skipped"]) == (2, 0, 1)
    by_id = {r["receipt_id"]: r for r in result["rows"]}
    assert by_id[3000001]["status"] == "ok" and by_id[3000001]["carrier_name"] == "UPS"
    assert by_id[3000001]["shipments"][0]["tracking_code"] == "1ZEXAMPLE01"
    assert by_id[3000003] == {"receipt_id": 3000003, "carrier_name": "UPS",
                              "tracking_code": "1ZEXAMPLE03", "status": "skipped",
                              "code": "not_waiting"}
    posts = [r for r in fake.requests if r.method == "POST"]
    assert [r.url.path.split("/")[-2] for r in posts] == ["3000001", "3000002"]
    assert json.loads(posts[1].content) == {"tracking_code": "9400EXAMPLE02", "carrier_name": "USPS",
                                            "note_to_buyer": "Thanks"}
    row_events = [d for t, d in events if t == "job-event" and d["type"] == "row"]
    assert [e["data"]["status"] for e in row_events] == ["ok", "ok", "skipped"]
    assert job["state"]["sent"] == 2 and len(job["state"]["rows"]) == 3
    assert [q["receipt_id"] for q in job["state"]["queue"]] == [3000001, 3000002, 3000003]
    assert web.ctx.shop_prefs()["last_carrier"]["TR"] in ("UPS", "USPS")
    notes = web.client.get("/api/notifications").json()["items"]
    # The Panel's sub-line names the carriers the send used, most used first.
    assert notes[0]["key"] == "notify.shipped"
    assert notes[0]["params"] == {"n": 2, "carriers": ["UPS", "USPS"]}


def test_a_ship_job_waiting_behind_another_write_job_can_be_found_again(web):
    # Write jobs run one at a time: a send waits behind a long Tasarım Yükle run. A page
    # that comes back meanwhile finds the job in /api/jobs with its count (params.n, the
    # "Gönderiliyor 0/n" label) and its rows in state.queue, saved when the job was made
    # (orders.js resumeJob / restoreQueue), not only once it runs.
    receipts = [make_receipt(1), make_receipt(2)]
    fake, _store = setup_orders(web, receipts)
    for n in (1, 2):
        fake.add("POST", f"{RECEIPTS}/{3000000 + n}/tracking", shipment_answer)
    release = threading.Event()
    blocker = web.ctx.jobs.start("designs", "designs:job.title", lambda job: release.wait(10),
                                 refresh_status=False)
    try:
        rows = [{"receipt_id": 3000001, "carrier_name": "UPS", "tracking_code": "1ZEXAMPLE01"},
                {"receipt_id": 3000002, "carrier_name": "USPS", "tracking_code": "9400EXAMPLE02"}]
        job = web.client.post("/api/orders/ship",
                              json={"rows": rows, "country": "TR", "confirm": True}).json()
        listed = web.client.get("/api/jobs", params={"kind": "orders"}).json()
        assert [j["id"] for j in listed] == [job["id"]]
        waiting = web.client.get(f"/api/jobs/{job['id']}").json()
        assert waiting["status"] == "queued" and waiting["params"] == {"n": 2}
        assert waiting["state"]["total"] == 2 and waiting["state"]["country"] == "TR"
        assert waiting["state"]["queue"] == [
            {"receipt_id": 3000001, "carrier_name": "UPS", "tracking_code": "1ZEXAMPLE01"},
            {"receipt_id": 3000002, "carrier_name": "USPS", "tracking_code": "9400EXAMPLE02"}]
        assert "rows" not in waiting["state"]  # nothing sent yet
    finally:
        release.set()
    assert wait_for_job(web, blocker.id)["status"] == "done"
    done = wait_for_job(web, job["id"])
    assert done["status"] == "done"
    assert [(q["receipt_id"], q["tracking_code"]) for q in done["state"]["queue"]] == [
        (3000001, "1ZEXAMPLE01"), (3000002, "9400EXAMPLE02")]


def test_tracking_restricted_stops_after_the_first_refusal(web):
    receipts = [make_receipt(n) for n in range(1, 6)]
    fake, _store = setup_orders(web, receipts)
    for n in range(1, 6):
        fake.error("POST", f"{RECEIPTS}/{3000000 + n}/tracking", 403, "Unauthorized")
    rows = [{"receipt_id": 3000000 + n, "carrier_name": "UPS", "tracking_code": f"1Z{n}"}
            for n in range(1, 6)]
    job = web.client.post("/api/orders/ship", json={"rows": rows, "confirm": True}).json()
    final = wait_for_job(web, job["id"])
    result = final["result"]
    assert result["restricted"] is True and result["stopped"] == "tracking_restricted"
    assert (result["sent"], result["failed"], result["skipped"]) == (0, 1, 4)
    assert result["rows"][0]["code"] == "tracking_restricted"
    assert "Shop Manager" in result["rows"][0]["message"]
    assert len([r for r in fake.requests if r.method == "POST"]) == 1
    assert web.ctx.shop_prefs()["tracking_restricted"] is True
    assert web.client.get("/api/orders/summary").json()["tracking_restricted"] is True
    notes = web.client.get("/api/notifications").json()["items"]
    assert notes[0]["key"] == "notify.restricted"


def test_a_later_success_clears_the_restriction(web):
    fake, _store = setup_orders(web, [make_receipt(1)])
    web.ctx.update_shop_prefs(tracking_restricted=True)
    fake.add("POST", f"{RECEIPTS}/3000001/tracking", shipment_answer)
    job = web.client.post("/api/orders/ship", json={"confirm": True, "rows": [
        {"receipt_id": 3000001, "carrier_name": "UPS", "tracking_code": "1Z1"}]}).json()
    assert wait_for_job(web, job["id"])["result"]["sent"] == 1
    assert "tracking_restricted" not in web.ctx.shop_prefs()


def test_one_failed_row_does_not_stop_the_others(web):
    fake, _store = setup_orders(web, [make_receipt(1), make_receipt(2)])
    fake.error("POST", f"{RECEIPTS}/3000001/tracking", 400, "tracking_code is invalid")
    fake.add("POST", f"{RECEIPTS}/3000002/tracking", shipment_answer)
    job = web.client.post("/api/orders/ship", json={"confirm": True, "rows": [
        {"receipt_id": 3000001, "carrier_name": "UPS", "tracking_code": "x"},
        {"receipt_id": 3000002, "carrier_name": "UPS", "tracking_code": "1Z2"}]}).json()
    result = wait_for_job(web, job["id"])["result"]
    assert (result["sent"], result["failed"]) == (1, 1)
    assert result["rows"][0]["code"] == "etsy_error" and result["stopped"] is None
    assert result["rows"][0]["message"] == "tracking_code is invalid"
    assert result["rows"][0]["http_status"] == 400


@pytest.mark.parametrize("rows, message", [
    ([], "rows is empty"),
    ([{"receipt_id": 3000001, "carrier_name": "", "tracking_code": "1Z1"}], "carrier_name is required"),
    ([{"receipt_id": 3000001, "carrier_name": "UPS", "tracking_code": " "}], "tracking_code is required"),
    ([{"receipt_id": "abc", "carrier_name": "UPS", "tracking_code": "1Z"}], "whole number"),
    ([{"receipt_id": 3000001, "carrier_name": "UPS", "tracking_code": "1Z"},
      {"receipt_id": 3000001, "carrier_name": "UPS", "tracking_code": "1Z"}], "twice"),
])
def test_ship_validation_sends_nothing(web, rows, message):
    fake, _store = setup_orders(web, [make_receipt(1)])
    resp = web.client.post("/api/orders/ship", json={"rows": rows})
    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] == "invalid" and message in error["message"]
    assert not [r for r in fake.requests if r.method == "POST"]
    assert web.client.get("/api/jobs", params={"kind": "orders"}).json() == []


def test_ship_rejects_a_carrier_etsy_does_not_list(web):
    fake, _store = setup_orders(web, [make_receipt(1)])
    resp = web.client.post("/api/orders/ship", json={"country": "TR", "rows": [
        {"receipt_id": 3000001, "carrier_name": "Pigeon Post", "tracking_code": "1Z1"}]})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["params"]["rows"][0]["receipt_id"] == 3000001
    assert "Pigeon Post" in error["message"]
    # "other" is always allowed (createReceiptShipment docs).
    fake.add("POST", f"{RECEIPTS}/3000001/tracking", shipment_answer)
    ok = web.client.post("/api/orders/ship", json={"country": "TR", "confirm": True, "rows": [
        {"receipt_id": 3000001, "carrier_name": "Other", "tracking_code": "1Z1"}]})
    assert ok.status_code == 200
    assert wait_for_job(web, ok.json()["id"])["result"]["rows"][0]["carrier_name"] == "other"


# --- CSV ----------------------------------------------------------------------------------------


def test_import_tracking_csv_comma_and_aliases(web):
    setup_orders(web, [])
    data = (
        "receipt_id,tracking_code,carrier_name,note_to_buyer\r\n"
        "3000001,1ZEXAMPLE1,UPS,\r\n"
        "#3000002,9400EXAMPLE2,usps,Enjoy\r\n"
        "3000003,,UPS,\r\n"
        "not-a-number,1Z9,UPS,\r\n"
        ",,,\r\n"
        "3000001,1ZEXAMPLE1B,UPS,\r\n"
    ).encode("utf-8-sig")
    resp = web.client.post("/api/orders/import-tracking", content=data,
                           headers={"Content-Type": "text/csv"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(r["receipt_id"], r["tracking_code"], r["carrier_name"]) for r in body["rows"]] == [
        (3000002, "9400EXAMPLE2", "usps"), (3000001, "1ZEXAMPLE1B", "UPS")]
    assert body["rows"][0]["note_to_buyer"] == "Enjoy"
    assert body["skipped"] == 1
    assert body["errors"][0]["line"] == 5 and "whole number" in body["errors"][0]["message"]


def test_import_tracking_csv_turkish_excel():
    data = "Sipariş No;Takip No;Kargo\r\n3000001;TR123 456;PTT Kargo\r\n".encode("cp1254")
    body = orders_api.parse_tracking_csv(data)
    assert body["rows"][0]["receipt_id"] == 3000001
    assert body["rows"][0]["tracking_code"] == "TR123 456"
    assert body["rows"][0]["carrier_name"] == "PTT Kargo"


def test_import_tracking_csv_needs_the_columns(web):
    setup_orders(web, [])
    resp = web.client.post("/api/orders/import-tracking", content=b"name,city\nA,B\n")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_csv"
    assert resp.json()["error"]["params"]["missing"] == ["receipt_id", "tracking_code"]
    empty = web.client.post("/api/orders/import-tracking", content=b"")
    assert empty.json()["error"]["code"] == "invalid_csv"


def test_export_csv_never_hands_a_spreadsheet_a_formula(web):
    receipts = [make_receipt(1)]
    receipts[0]["name"] = '=HYPERLINK("http://example.invalid","x")'
    setup_orders(web, receipts)
    resp = web.client.get("/api/orders/export.csv", params={"tab": "unshipped"})
    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
    assert rows[0]["buyer_name"].startswith("'=")
    assert orders_api.sheet_safe("-5") == "'-5" and orders_api.sheet_safe("Ada") == "Ada"


def test_export_csv_round_trips_into_the_import(web):
    receipts = [make_receipt(1), make_receipt(2, shipped=True)]
    setup_orders(web, receipts)
    resp = web.client.get("/api/orders/export.csv", params={"tab": "all"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
    assert list(rows[0]) == orders_api.EXPORT_COLUMNS
    assert rows[0]["receipt_id"] == "3000001" and rows[0]["buyer_name"] == "Example Buyer1"
    assert rows[1]["tracking_code"] == "1ZEXAMPLE2" and rows[1]["status"] == "shipped"
    assert "@" not in resp.content.decode("utf-8-sig")
    assert "Example Street" not in resp.content.decode("utf-8-sig")
    # POST adds the numbers typed on the page; the import reads them back.
    typed = web.client.post("/api/orders/export.csv", json={
        "tab": "unshipped", "edits": {"3000001": {"carrier_name": "DHL", "tracking_code": "40183355"}}})
    back = orders_api.parse_tracking_csv(typed.content)
    assert back["rows"] == [{"receipt_id": 3000001, "tracking_code": "40183355",
                             "carrier_name": "DHL", "note_to_buyer": "", "line": 2}]


# --- review fixes: escaped text, the send guard, the confirm, this month's shipments ----------------


def test_titles_and_names_arrive_as_plain_text(web):
    # Etsy sends what sellers and buyers typed HTML-escaped; the page shows it as typed.
    receipt = make_receipt(1)
    receipt["name"] = "Example O&#39;Buyer"
    receipt["transactions"][0]["title"] = "Mom&#39;s Mug &amp; &quot;Best&quot; Gift"
    receipt["transactions"][0]["variations"][0]["formatted_value"] = "11&quot; x 14&quot;"
    shipped = make_receipt(2, shipped=True)
    shipped["transactions"][0]["title"] = "Tom &amp; Jerry Poster"
    setup_orders(web, [receipt, shipped])
    rows = web.client.get("/api/orders", params={"tab": "unshipped"}).json()["rows"]  # a scan
    item = rows[0]["items"][0]
    assert item["title"] == "Mom's Mug & \"Best\" Gift"
    assert item["variations"][0]["value"] == "11\" x 14\""
    assert rows[0]["buyer"] == "Example O."
    page = web.client.get("/api/orders", params={"tab": "shipped"}).json()["rows"]  # one page
    assert page[0]["items"][0]["title"] == "Tom & Jerry Poster"
    found = web.client.get("/api/orders", params={"tab": "unshipped", "q": "mom's mug"}).json()
    assert [r["receipt_id"] for r in found["rows"]] == [3000001]
    text = web.client.get("/api/orders/export.csv", params={"tab": "all"}).content.decode("utf-8-sig")
    assert "Mom's Mug & \"\"Best\"\" Gift" in text
    assert "&#39;" not in text and "&amp;" not in text and "&quot;" not in text


@pytest.mark.parametrize("confirm", [None, False, "true", 1])
def test_sending_tracking_needs_confirm_true(web, confirm):
    fake, _store = setup_orders(web, [make_receipt(1)])
    fake.add("POST", f"{RECEIPTS}/3000001/tracking", shipment_answer)
    body = {"rows": [{"receipt_id": 3000001, "carrier_name": "UPS", "tracking_code": "1Z1"}]}
    if confirm is not None:
        body["confirm"] = confirm
    resp = web.client.post("/api/orders/ship", json=body)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "confirm_required"
    assert not [r for r in fake.requests if r.method == "POST"]
    assert web.client.get("/api/jobs", params={"kind": "orders"}).json() == []


def test_orders_beyond_the_waiting_scan_are_checked_one_by_one(web, monkeypatch):
    # A shop with more waiting orders than one scan holds: an older one is still sent,
    # after getShopReceipt says it is waiting; one that is not waiting is still skipped.
    monkeypatch.setattr(orders_api, "SCAN_LIMIT", 1)
    receipts = [make_receipt(1), make_receipt(2), make_receipt(3)]
    fake, _store = setup_orders(web, receipts)
    older = {k: v for k, v in receipts[1].items() if not k.startswith("_")}
    fake.add("GET", f"{RECEIPTS}/3000002", older)
    gone = {k: v for k, v in make_receipt(4, shipped=True).items() if not k.startswith("_")}
    fake.add("GET", f"{RECEIPTS}/3000004", gone)
    for n in (1, 2, 4, 5):
        fake.add("POST", f"{RECEIPTS}/{3000000 + n}/tracking", shipment_answer)
    rows = [{"receipt_id": 3000000 + n, "carrier_name": "UPS", "tracking_code": f"1Z{n}"}
            for n in (1, 2, 4, 5)]
    job = web.client.post("/api/orders/ship", json={"rows": rows, "confirm": True}).json()
    result = wait_for_job(web, job["id"])["result"]
    by_id = {r["receipt_id"]: r for r in result["rows"]}
    assert by_id[3000001]["status"] == "ok"  # in the scan
    assert by_id[3000002]["status"] == "ok"  # older than the scan, still waiting
    assert by_id[3000004]["code"] == "not_waiting"  # shipped already
    assert by_id[3000005]["code"] == "not_waiting"  # Etsy has no such order (404)
    posts = [r.url.path.split("/")[-2] for r in fake.requests if r.method == "POST"]
    assert posts == ["3000001", "3000002"]
    looked_up = [r.url.path.rsplit("/", 1)[-1] for r in fake.requests
                 if r.method == "GET" and r.url.path.rsplit("/", 1)[-1].startswith("30000")]
    assert looked_up == ["3000002", "3000004", "3000005"]  # not the one the scan held


def test_a_complete_scan_is_the_answer_without_asking_again(web):
    fake, _store = setup_orders(web, [make_receipt(1), make_receipt(2, shipped=True)])
    fake.add("POST", f"{RECEIPTS}/3000002/tracking", shipment_answer)
    job = web.client.post("/api/orders/ship", json={"confirm": True, "rows": [
        {"receipt_id": 3000002, "carrier_name": "UPS", "tracking_code": "1Z2"}]}).json()
    result = wait_for_job(web, job["id"])["result"]
    assert result["rows"][0]["code"] == "not_waiting"
    assert not [r for r in fake.requests if r.url.path.endswith("/3000002")]
    assert not [r for r in fake.requests if r.method == "POST"]


def test_shipped_this_month_counts_shipments_not_order_dates(web, monkeypatch):
    start = 1790000000
    monkeypatch.setattr(orders_api, "month_start", lambda now=None: start)
    late = make_receipt(1, shipped=True)  # placed last month, shipped this month: counts
    late["created_timestamp"] = start - 5 * 86400
    late["shipments"][0]["shipment_notification_timestamp"] = start + 3600
    late["updated_timestamp"] = start + 3600
    early = make_receipt(2, shipped=True)  # shipped last month, delivered (changed) this month
    early["created_timestamp"] = start - 9 * 86400
    early["shipments"][0]["shipment_notification_timestamp"] = start - 7 * 86400
    early["updated_timestamp"] = start + 7200
    fixed = make_receipt(3, shipped=True)  # shipped last month, tracking corrected this month
    fixed["created_timestamp"] = start - 9 * 86400
    fixed["shipments"] = [
        {"carrier_name": "UPS", "tracking_code": "1ZA", "shipment_notification_timestamp": start - 86400},
        {"carrier_name": "UPS", "tracking_code": "1ZB", "shipment_notification_timestamp": start + 100},
    ]
    fixed["updated_timestamp"] = start + 100
    bare = make_receipt(4, shipped=True)  # marked shipped without a shipment, placed this month
    bare["created_timestamp"] = start + 50
    bare["shipments"] = []
    bare["updated_timestamp"] = start + 60
    old = make_receipt(5, shipped=True)  # nothing happened this month
    old["created_timestamp"] = start - 20 * 86400
    old["shipments"][0]["shipment_notification_timestamp"] = start - 19 * 86400
    old["updated_timestamp"] = start - 19 * 86400
    _fake, store = setup_orders(web, [late, early, fixed, bare, old])
    data = web.client.get("/api/orders/summary").json()
    assert data["shipped_month"] == 2 and data["shipped_month_partial"] is False
    scan = [q for q in store.queries if "min_last_modified" in q]
    assert scan and scan[0]["min_last_modified"] == str(start)
    assert scan[0]["was_shipped"] == "true" and scan[0]["was_paid"] == "true"
    assert scan[0]["sort_on"] == "updated"
    assert not any("min_created" in q for q in store.queries)
    assert orders_api.shipped_since(late, start) and not orders_api.shipped_since(fixed, start)


def test_shipped_this_month_says_when_it_stopped_counting(web, monkeypatch):
    monkeypatch.setattr(orders_api, "month_start", lambda now=None: 1790000000 - 86400 * 30)
    monkeypatch.setattr(orders_api, "SHIPPED_SCAN_LIMIT", 2)
    setup_orders(web, [make_receipt(n, shipped=True) for n in range(1, 5)])
    data = web.client.get("/api/orders/summary").json()
    assert data["shipped_month"] == 2 and data["shipped_month_partial"] is True
