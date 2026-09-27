"""drop.stream: the per-product run behind the web UI's Tasarım Yükle screen."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
from PIL import Image

from stallkit.drop import automation, catalog, mockup, stream
from stallkit.drop.template import Template
from stallkit.drop.workspace import Workspace
from stallkit.errors import AuthError, EtsyApiError, ValidationError

SHOP = "123"


def _artwork(path, colour=(200, 60, 40)):
    """A transparent design: a filled square on a see-through ground."""
    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for x in range(10, 30):
        for y in range(10, 30):
            image.putpixel((x, y), (*colour, 255))
    image.save(path)
    return path


def _photo(path, colour=(90, 120, 150)):
    Image.new("RGB", (40, 40), colour).save(path)
    return path


@pytest.fixture
def studio(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "studio").create()
    _photo(ws.mockups / "tshirt-white.jpg", (240, 240, 240))
    _photo(ws.mockups / "mug-white.jpg", (250, 250, 250))
    template = Template(1000001, fields={
        "taxonomy_id": 1, "price": 21, "quantity": 5, "who_made": "i_did",
        "when_made": "made_to_order", "type": "physical", "shipping_profile_id": 55,
    }, description="Soft cotton tee.", tags=["gift idea", "retro style"])
    ws.write_template(template.to_dict())

    # Compositing at the real 2000 px output is slow and beside the point here.
    def small_compose(design, template_image, out, *, area=None, **_kw):
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (30, 30), (200, 200, 200)).save(out, "JPEG")
        return out

    def small_flat(design, out, **_kw):
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (30, 30), (255, 255, 255)).save(out, "JPEG")
        return out

    monkeypatch.setattr(mockup, "compose", small_compose)
    monkeypatch.setattr(mockup, "flatten_design", small_flat)
    return ws, template


class Client:
    """A stand-in for EtsyClient with the calls the stream makes."""

    def __init__(self, ws, *, search_delay=0.0):
        self.ws = ws
        self.lock = threading.Lock()
        self.creates: list[dict] = []
        self.images: list[tuple[int, str, int]] = []
        self.searches: list[str] = []
        self.search_delay = search_delay
        self.active_searches = 0
        self.max_active_searches = 0
        self.fail_create: dict[str, BaseException] = {}  # title substring -> error
        self.fail_search: BaseException | None = None
        self.next_id = 1000001

    def shop_id(self):
        return int(SHOP)

    def listing_inventory(self, listing_id):
        return {"products": [{"property_values": [], "offerings": [
            {"price": {"amount": 2100, "divisor": 100}, "quantity": 5, "is_enabled": True}]}]}

    def search_active_listings(self, *, keywords, max_items=100, **filters):
        with self.lock:
            self.searches.append(keywords)
            self.active_searches += 1
            self.max_active_searches = max(self.max_active_searches, self.active_searches)
        try:
            if self.search_delay:
                time.sleep(self.search_delay)
            if self.fail_search is not None:
                raise self.fail_search
            words = keywords.split()
            return [
                {"title": f"{keywords} shirt vintage gift {n}",
                 "tags": [f"{words[0]} tee", "vintage gift", f"extra tag {n % 12}"],
                 "price": {"amount": 2000, "divisor": 100, "currency_code": "USD"},
                 "num_favorers": n}
                for n in range(30)
            ]
        finally:
            with self.lock:
                self.active_searches -= 1

    def create_draft_listing(self, fields):
        history = json.loads((self.ws.root / "upload-history.json").read_text(encoding="utf-8"))
        pending = [name for name, entry in history[SHOP].items() if entry["status"] == "pending"]
        assert len(pending) == 1, "intent is saved before the create"
        for needle, error in self.fail_create.items():
            if needle in fields["title"].lower():
                raise error
        with self.lock:
            self.creates.append(dict(fields))
            listing_id = self.next_id
            self.next_id += 1
        return {"listing_id": listing_id}

    def upload_listing_image(self, listing_id, image, *, rank):
        with self.lock:
            self.images.append((listing_id, image.name, rank))
        return {}


class Events:
    def __init__(self):
        self.lock = threading.Lock()
        self.items: list[tuple[str, str, str, dict]] = []

    def __call__(self, name, step, status, data):
        with self.lock:
            self.items.append((name, step, status, data))

    def of(self, name):
        return [(step, status) for n, step, status, _ in self.items if n == name]

    def outcome(self, name):
        return next(data for n, step, status, data in reversed(self.items)
                    if n == name and step == "item" and status != "waiting")


def _run(ws, template, client, **kw):
    kw.setdefault("mockups", catalog.enabled_mockups(ws))
    return stream.run_stream(ws, template, client, **kw)


def _history(ws):
    return json.loads((ws.root / "upload-history.json").read_text(encoding="utf-8"))


def test_one_product_walks_the_six_steps_in_order(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    events = Events()
    report = _run(ws, template, Client(ws), on_event=events)

    item = report.items[0]
    assert item.status == stream.OK and item.listing_id == 1000001
    assert item.steps == {step: "done" for step in stream.STEPS}
    finished = [step for step, status in events.of("retro-mountain-sunset.png")
                if status in ("done", "warn")]
    assert finished == list(stream.STEPS)
    starts = [step for step, status in events.of("retro-mountain-sunset.png")
              if status == "running"]
    assert list(dict.fromkeys(starts)) == list(stream.STEPS)
    outcome = events.outcome("retro-mountain-sunset.png")
    assert outcome["listing_id"] == 1000001
    assert outcome["title"].startswith("Retro Mountain Sunset")
    assert len(outcome["tags"]) == 13
    assert all(path.startswith("3-DRAFTS/") for path in outcome["images"])
    batch = next(data for name, step, _s, data in events.items if step == "batch")
    assert [i["name"] for i in batch["items"]] == ["retro-mountain-sunset.png"]
    assert _history(ws)[SHOP]["retro-mountain-sunset.png"]["status"] == "ok"
    assert report.csv_path is not None and report.csv_path.is_file()


def test_image_counts_mockups_plus_flat_opaque_as_is_and_folder_photos(studio):
    ws, template = studio
    _artwork(ws.products / "cat-mom-club.png")
    _photo(ws.products / "ocean-waves-photo.jpg")
    folder = ws.products / "desert cactus print"
    folder.mkdir()
    for name in ("2-back.jpg", "1-front.jpg", "10-detail.jpg"):
        _photo(folder / name)
    client = Client(ws)
    report = _run(ws, template, client)

    by_name = {item.name: item for item in report.items}
    art = by_name["cat-mom-club.png"]
    assert art.mode == "composited" and len(art.images) == 3  # 2 mockups + the flat design
    assert by_name["ocean-waves-photo.jpg"].mode == "as_is"
    assert [p.name for p in by_name["ocean-waves-photo.jpg"].images] == ["ocean-waves-photo.jpg"]
    photos = by_name["desert cactus print"]
    assert photos.mode == "photos"
    assert [p.name for p in photos.images] == ["1-front.jpg", "2-back.jpg", "10-detail.jpg"]
    per_listing: dict[int, int] = {}
    for listing_id, _name, _rank in client.images:
        per_listing[listing_id] = per_listing.get(listing_id, 0) + 1
    assert sorted(per_listing.values()) == [1, 3, 3]


def test_without_the_flat_render_only_the_mockups_go_up(studio):
    ws, template = studio
    _artwork(ws.products / "stay-wild-moon.png")
    report = _run(ws, template, Client(ws), include_flat=False)
    assert len(report.items[0].images) == 2
    assert all("--flat" not in p.name for p in report.items[0].images)


def test_drafts_never_carry_a_state_or_an_id(studio):
    ws, template = studio
    _artwork(ws.products / "but-first-coffee.png")
    client = Client(ws)
    _run(ws, template, client)
    fields = client.creates[0]
    assert "state" not in fields and "listing_id" not in fields
    assert fields["price"] == 21.0 and fields["shipping_profile_id"] == 55


def test_research_runs_once_per_concept_and_products_prepare_in_parallel(studio):
    ws, template = studio
    for n in range(4):
        _artwork(ws.products / f"{n + 1:03d}-wildflower-botanical.png")
    for name in ("cat-mom-club.png", "ocean-waves.png", "desert-cactus.png"):
        _artwork(ws.products / name)
    client = Client(ws, search_delay=0.15)
    report = _run(ws, template, client, concurrency=3, use_cache=False)

    assert report.created == 7
    assert sorted(client.searches) == sorted(
        ["wildflower botanical", "cat mom club", "ocean waves", "desert cactus"]
    )
    assert client.max_active_searches >= 2, "different concepts are researched side by side"
    assert report.researched == 4
    listing_ids = [item.listing_id for item in report.items]
    assert listing_ids == sorted(listing_ids), "drafts are created in folder order"


def test_a_junk_file_name_fails_only_that_product(studio):
    ws, template = studio
    _artwork(ws.products / "IMG_2043.png")
    _artwork(ws.products / "retro-mountain-sunset.png")
    events = Events()
    client = Client(ws)
    report = _run(ws, template, client, on_event=events)

    junk = next(item for item in report.items if item.name == "IMG_2043.png")
    assert junk.status == stream.FAILED
    assert junk.error.code == "junk_name" and junk.steps["mockup"] == "error"
    assert junk.steps["draft"] == "todo"
    good = next(item for item in report.items if item.name == "retro-mountain-sunset.png")
    assert good.status == stream.OK
    assert len(client.creates) == 1
    assert events.outcome("IMG_2043.png")["problem"]["code"] == "junk_name"
    assert "IMG_2043.png" not in _history(ws)[SHOP], "never attempted, so still pending"


def test_a_product_that_fails_its_check_does_not_stop_the_batch(studio):
    ws, template = studio
    folder = ws.products / "retro sunset bundle"
    folder.mkdir()
    for n in range(21):
        _photo(folder / f"{n:02d}.jpg")
    _artwork(ws.products / "ocean-waves.png")
    broken = ws.products / "cat-mom-club.jpg"
    buffer = Image.new("RGB", (300, 300), (1, 2, 3))
    buffer.save(broken, "JPEG", quality=95)
    whole = broken.read_bytes()
    broken.write_bytes(whole[: len(whole) // 2])  # a copy cut short

    client = Client(ws)
    report = _run(ws, template, client)
    by_name = {item.name: item for item in report.items}
    assert by_name["retro sunset bundle"].error.code == "too_many_images"
    assert by_name["cat-mom-club.jpg"].error.code == "invalid_image"
    assert by_name["cat-mom-club.jpg"].steps["check"] == "error"
    assert by_name["ocean-waves.png"].status == stream.OK
    assert len(client.creates) == 1


def test_the_history_guards_against_a_second_draft(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    client = Client(ws)
    first = _run(ws, template, client)
    assert first.created == 1
    second = _run(ws, template, client)
    assert second.items == []
    assert second.already_done == ["retro-mountain-sunset.png"]
    assert len(client.creates) == 1


def test_an_uncertain_attempt_is_kept_for_review_and_not_retried(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    client = Client(ws)
    lost = EtsyApiError(0, "network error: read timed out — the request may still have been "
                           "accepted by Etsy.", method="POST", path="/shops/123/listings")
    client.fail_create["retro"] = lost
    report = _run(ws, template, client)
    item = report.items[0]
    assert item.status == stream.FAILED and item.error.code == "draft_uncertain"
    assert _history(ws)[SHOP]["retro-mountain-sunset.png"]["status"] == "error"
    again = _run(ws, template, client)
    assert again.items == [] and again.needs_review


def test_a_create_etsy_refused_leaves_the_product_free_to_retry(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    client = Client(ws)
    client.fail_create["retro"] = EtsyApiError(400, "Invalid taxonomy", method="POST",
                                               path="/shops/123/listings")
    report = _run(ws, template, client)
    assert report.items[0].error.code == "draft_refused"
    assert "retro-mountain-sunset.png" not in _history(ws)[SHOP]
    client.fail_create.clear()
    again = _run(ws, template, client)
    assert again.created == 1


def test_a_connection_that_never_opened_is_a_refusal_too(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    _artwork(ws.products / "ocean-waves.png")
    client = Client(ws)
    error = EtsyApiError(0, "network error: offline", method="POST", path="/shops/123/listings")
    error.__cause__ = httpx.ConnectError("offline")
    client.fail_create["ocean"] = error
    report = _run(ws, template, client)
    ocean = next(item for item in report.items if item.name == "ocean-waves.png")
    assert ocean.error.code == "draft_refused"
    assert "ocean-waves.png" not in _history(ws)[SHOP]
    assert report.stopped is not None and report.stopped.code == "offline"
    retro = next(item for item in report.items if item.name == "retro-mountain-sunset.png")
    assert retro.status == stream.CANCELLED
    assert retro.error.code == "stopped" and retro.error.params["reason"] == "offline"


def test_a_lost_sign_in_stops_the_rest_without_marking_them(studio):
    ws, template = studio
    for name in ("a-retro-sunset.png", "b-ocean-waves.png", "c-cat-mom-club.png"):
        _artwork(ws.products / name)
    client = Client(ws)
    client.fail_create["retro"] = AuthError("refresh token revoked")
    report = _run(ws, template, client)
    assert [item.status for item in report.items] == ["error", "cancelled", "cancelled"]
    assert report.stopped.code == "reconnect"
    assert _history(ws)[SHOP] == {}, "nothing reached Etsy, so nothing is recorded"
    assert client.creates == []


def test_repeated_failures_stop_the_batch(studio):
    ws, template = studio
    for n in range(5):
        _artwork(ws.products / f"{n + 1}-retro-design-{'abcde'[n]}.png")
    client = Client(ws)
    client.fail_create["retro"] = EtsyApiError(400, "Shipping profile invalid", method="POST",
                                               path="/shops/123/listings")
    report = _run(ws, template, client)
    statuses = [item.status for item in report.items]
    assert statuses[:3] == ["error"] * 3 and statuses[3:] == ["cancelled"] * 2
    assert report.stopped.code == "repeated_failures"


def test_cancel_finishes_the_draft_in_flight_and_starts_nothing_new(studio):
    ws, template = studio
    for name in ("a-retro-sunset.png", "b-ocean-waves.png", "c-cat-mom-club.png",
                 "d-desert-cactus.png"):
        _artwork(ws.products / name)
    cancel = threading.Event()

    class Cancelling(Client):
        def upload_listing_image(self, listing_id, image, *, rank):
            cancel.set()  # asked to stop while the first draft is being created
            return super().upload_listing_image(listing_id, image, rank=rank)

    client = Cancelling(ws)
    report = _run(ws, template, client, cancel=cancel)
    assert report.cancelled
    assert report.items[0].status == stream.OK
    assert len({listing_id for listing_id, _n, _r in client.images}) == 1
    assert len(client.images) == 3, "the draft in flight got all its images"
    assert [item.status for item in report.items[1:]] == ["cancelled"] * 3
    assert all(item.error is None for item in report.items[1:])
    assert len(client.creates) == 1


def test_a_research_failure_is_a_warning_not_an_error(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    client = Client(ws)
    client.fail_search = EtsyApiError(503, "unavailable", method="GET", path="/listings/active")
    report = _run(ws, template, client, use_cache=False)
    item = report.items[0]
    assert item.status == stream.OK
    assert item.steps["research"] == "warn"
    assert "no_market_data" in [w.code for w in item.warnings]


def test_a_dry_run_checks_everything_and_writes_nothing_to_etsy(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    _artwork(ws.products / "IMG_0001.png")
    report = _run(ws, template, None, dry_run=True)
    statuses = {item.name: item.status for item in report.items}
    assert statuses == {"retro-mountain-sunset.png": stream.CHECKED, "IMG_0001.png": stream.FAILED}
    assert report.items[1].steps["draft"] == "todo"
    assert not (ws.root / "upload-history.json").exists()
    text = report.csv_path.read_text(encoding="utf-8-sig")
    assert "retro-mountain-sunset.png" in text and "IMG_0001" not in text


def test_a_template_that_cannot_make_a_draft_stops_before_any_work(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    template.fields["price"] = -1
    client = Client(ws)
    with pytest.raises(ValidationError, match="template listing cannot make a draft"):
        _run(ws, template, client)
    assert client.creates == [] and list(ws.drafts.iterdir()) == []


def test_another_run_holding_the_lock_is_refused(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    (ws.root / ".auto-upload.lock").write_text("4242")
    with pytest.raises(automation.UploadLocked):
        _run(ws, template, Client(ws))


def test_too_many_mockups_for_one_listing_is_refused(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    many = [ws.mockups / "tshirt-white.jpg"] * 20
    with pytest.raises(ValidationError, match="Etsy allows 20"):
        _run(ws, template, Client(ws), mockups=many)


def test_transparent_photos_in_a_ready_folder_are_flagged(studio):
    ws, template = studio
    folder = ws.products / "mountain sunset shirt"
    folder.mkdir()
    _artwork(folder / "1-front.png")
    _photo(folder / "2-back.jpg")
    report = _run(ws, template, Client(ws))
    item = report.items[0]
    assert item.status == stream.OK
    assert item.steps["mockup"] == "warn"
    assert [w.code for w in item.warnings if w.step == "mockup"] == ["transparent_photos"]


def test_a_template_listing_gone_from_etsy_stops_before_the_plan(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    events = Events()

    class Gone(Client):
        def listing_inventory(self, listing_id):
            raise EtsyApiError(404, "Listing not found", method="GET", path="/listings/1/inventory")

    with pytest.raises(stream.TemplateGone):
        _run(ws, template, Gone(ws), on_event=events)
    assert events.items == []
    assert not (ws.root / ".auto-upload.lock").exists()


# --- failure paths (test-gaps) -----------------------------------------------------------------


def test_an_image_that_fails_after_the_create_leaves_a_partial_draft_on_record(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    _artwork(ws.products / "ocean-waves.png")

    class ThirdImageFails(Client):
        def upload_listing_image(self, listing_id, image, *, rank):
            if listing_id == 1000001 and rank == 2:
                raise EtsyApiError(400, "bad image", method="POST", path="/images")
            return super().upload_listing_image(listing_id, image, rank=rank)

    events = Events()
    report = _run(ws, template, ThirdImageFails(ws), on_event=events)
    by_name = {item.name: item for item in report.items}
    partial = by_name["ocean-waves.png"]
    assert partial.status == stream.PARTIAL and partial.listing_id == 1000001
    assert partial.steps["draft"] == "warn" and partial.images_uploaded == 1
    assert "partial" in [w.code for w in partial.warnings]
    entry = _history(ws)[SHOP]["ocean-waves.png"]
    assert entry["status"] == "partial" and entry["listing_id"] == 1000001
    assert entry["images_uploaded"] == 1
    assert by_name["retro-mountain-sunset.png"].status == stream.OK, "the next one still goes"
    again = _run(ws, template, Client(ws))
    assert again.items == [] and any("ocean-waves.png" in n for n in again.needs_review)


def test_a_busy_history_file_does_not_stop_the_run(studio, monkeypatch):
    # Windows: a reader (antivirus, OneDrive, the app's own status check) holds the
    # file open, and os.replace answers "Access denied" now and then.
    ws, template = studio
    for name in ("a-retro-sunset.png", "b-ocean-waves.png"):
        _artwork(ws.products / name)
    monkeypatch.setattr(automation, "REPLACE_FIRST_PAUSE", 0.001)
    real = automation.os.replace
    calls = {"n": 0}

    def sometimes_busy(src, dst):
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            raise PermissionError(13, "Access denied")
        return real(src, dst)

    monkeypatch.setattr(automation.os, "replace", sometimes_busy)
    report = _run(ws, template, Client(ws))
    assert [item.status for item in report.items] == [stream.OK, stream.OK]
    assert {e["status"] for e in _history(ws)[SHOP].values()} == {"ok"}


def test_the_lock_removed_mid_run_ends_the_run_normally(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    lock = ws.root / ".auto-upload.lock"

    class Unlocking(Client):
        def create_draft_listing(self, fields):
            if lock.exists():
                lock.unlink()
            return super().create_draft_listing(fields)

    report = _run(ws, template, Unlocking(ws))
    assert report.items[0].status == stream.OK and report.finished_at is not None


def test_a_design_too_large_to_decode_fails_only_that_product(studio):
    ws, template = studio
    Image.new("1", (13000, 13000)).save(ws.products / "huge-mountain-poster.png")
    _artwork(ws.products / "retro-mountain-sunset.png")
    client = Client(ws)
    with pytest.warns(Image.DecompressionBombWarning):
        report = _run(ws, template, client)
    by_name = {item.name: item for item in report.items}
    huge = by_name["huge-mountain-poster.png"]
    assert huge.status == stream.FAILED and huge.error.code == "too_many_pixels"
    assert huge.error.params["width"] == 13000 and huge.steps["mockup"] == "error"
    assert by_name["retro-mountain-sunset.png"].status == stream.OK
    assert len(client.creates) == 1


def test_a_finished_photo_over_etsys_limit_goes_up_smaller(studio, monkeypatch):
    from stallkit.drop import pipeline

    ws, template = studio
    photo = ws.products / "ocean-waves-photo.jpg"
    Image.effect_noise((500, 500), 90).convert("RGB").save(photo, quality=98)
    monkeypatch.setattr(pipeline, "MAX_IMAGE_BYTES", photo.stat().st_size // 3)
    client = Client(ws)
    report = _run(ws, template, client)
    item = report.items[0]
    assert item.status == stream.OK and item.mode == "as_is"
    shrunk = [w for w in item.warnings if w.step == "mockup"]
    assert [w.code for w in shrunk] == ["shrunk"]
    assert shrunk[0].params["name"] == "ocean-waves-photo.jpg"
    assert [name for _id, name, _rank in client.images] == ["ocean-waves-photo-jpg-etsy.jpg"]


def test_a_title_etsy_would_refuse_is_cleaned_and_said_so(studio, monkeypatch):
    from stallkit.drop import generate

    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    monkeypatch.setattr(generate, "build_title",
                        lambda seed, market: "Salt & Pepper & Co, $5 Mug \U0001f338")
    client = Client(ws)
    report = _run(ws, template, client)
    item = report.items[0]
    assert item.status == stream.OK and item.steps["title"] == "warn"
    assert "title_cleaned" in [w.code for w in item.warnings]
    assert client.creates[0]["title"] == "Salt & Pepper and Co, 5 Mug"


def test_a_title_with_nothing_etsy_accepts_fails_that_product(studio, monkeypatch):
    from stallkit.drop import generate

    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    monkeypatch.setattr(generate, "build_title", lambda seed, market: "\U0001f338\U0001f338")
    client = Client(ws)
    report = _run(ws, template, client)
    item = report.items[0]
    assert item.status == stream.FAILED and item.error.code == "invalid_title"
    assert item.steps["title"] == "error" and client.creates == []


def test_the_flat_design_is_marked_apart_from_the_mockups(studio):
    ws, template = studio
    _artwork(ws.products / "retro-mountain-sunset.png")
    events = Events()
    report = _run(ws, template, Client(ws), on_event=events)
    item = report.items[0]
    assert item.flat is not None and item.flat == item.images[-1]
    outcome = events.outcome("retro-mountain-sunset.png")
    assert outcome["flat"].endswith("--flat.jpg") and outcome["flat"] in outcome["images"]
    assert stream.item_summary(item, ws.root)["flat"] == outcome["flat"]


@pytest.mark.parametrize("junk", ["Adsız tasarım (3).png", "Untitled design (4).png",
                                  "image (1).png", "IMG_4432.png"])
def test_a_canva_or_camera_default_name_never_becomes_a_draft(studio, junk):
    ws, template = studio
    _artwork(ws.products / junk)
    _artwork(ws.products / "retro-mountain-sunset.png")
    client = Client(ws)
    report = _run(ws, template, client)
    by_name = {item.name: item for item in report.items}
    assert by_name[junk].status == stream.FAILED and by_name[junk].error.code == "junk_name"
    assert by_name["retro-mountain-sunset.png"].status == stream.OK
    assert len(client.creates) == 1 and junk not in _history(ws)[SHOP]
