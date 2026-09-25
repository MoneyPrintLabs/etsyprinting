import io
import json

import pytest
from PIL import Image
from typer.testing import CliRunner

from stallkit.cli import app
from stallkit.drop import automation, pipeline
from stallkit.drop.template import Template
from stallkit.drop.workspace import Workspace
from stallkit.errors import ValidationError


@pytest.fixture
def studio(tmp_path):
    ws = Workspace(tmp_path / "studio").create()
    folder = ws.products / "mountain sunset shirt"
    folder.mkdir()
    for name in ("10-detail.png", "2-back.png", "1-front.png"):
        Image.new("RGBA", (20, 20), (20, 30, 40, 100)).save(folder / name)
    template = Template(1, fields={"taxonomy_id": 1, "price": 20, "quantity": 5,
                                    "who_made": "i_did", "when_made": "made_to_order",
                                    "type": "physical"}, description="Cotton shirt.")
    ws.write_template(template.to_dict())
    return ws, template


class Client:
    def __init__(self, ws, fail=None):
        self.ws, self.fail = ws, fail
        self.creates = 0
        self.images = []

    def shop_id(self):
        return 123

    def search_active_listings(self, **kwargs):
        return iter([])

    def create_draft_listing(self, fields):
        self.creates += 1
        state = json.loads((self.ws.root / "upload-history.json").read_text())
        assert state["123"]["mountain sunset shirt"]["status"] == "pending"
        if self.fail == "create":
            raise OSError("response lost")
        return {"listing_id": 900}

    def upload_listing_image(self, listing_id, image, *, rank):
        state = json.loads((self.ws.root / "upload-history.json").read_text())
        assert state["123"]["mountain sunset shirt"]["listing_id"] == 900
        if self.fail == "image":
            raise OSError("upload interrupted")
        self.images.append((image.name, rank))
        return {}


def test_folder_is_one_listing_and_ready_pngs_are_not_composited(studio):
    ws, template = studio
    report = pipeline.run(ws, template)
    assert len(report.ready) == 1
    row = report.ready[0]
    assert row.seed.text == "mountain sunset shirt"
    assert [p.name for p in row.images] == ["1-front.png", "2-back.png", "10-detail.png"]
    assert all(p.parent == row.source for p in row.images)


def test_auto_uploads_all_images_and_second_run_does_not_duplicate(studio):
    ws, template = studio
    client = Client(ws)
    first = automation.run(ws, template, client=client)
    assert first.uploaded.created == 1
    assert client.images == [("1-front.png", 1), ("2-back.png", 2), ("10-detail.png", 3)]
    second = automation.run(ws, template, client=client)
    assert client.creates == 1
    assert second.already_done == ["mountain sunset shirt"]


@pytest.mark.parametrize("failure", ["create", "image"])
def test_uncertain_or_partial_upload_is_not_recreated(studio, failure):
    ws, template = studio
    client = Client(ws, failure)
    automation.run(ws, template, client=client)
    second = automation.run(ws, template, client=client)
    assert client.creates == 1
    assert second.needs_review


def test_cli_dry_run_is_offline_and_does_not_mark_uploaded(studio):
    ws, _ = studio
    result = CliRunner().invoke(app, ["drop", "auto", "--path", str(ws.root), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "validated" in result.output
    assert not (ws.root / "upload-history.json").exists()


def test_corrupt_image_aborts_before_any_upload(studio):
    ws, template = studio
    (ws.products / "mountain sunset shirt" / "2-back.png").write_text("broken")
    client = Client(ws)
    with pytest.raises(ValidationError, match="Invalid image"):
        automation.run(ws, template, client=client)
    assert client.creates == 0


def test_a_ready_photo_etsy_refuses_is_converted_and_the_row_says_so(studio):
    # Ready photos upload unchanged, so a .webp would reach Etsy as a .webp and be
    # refused after the draft existed. It is converted here, and never silently.
    ws, template = studio
    folder = ws.products / "mountain sunset shirt"
    (folder / "1-front.png").unlink()
    Image.new("RGB", (40, 40), (10, 20, 30)).save(folder / "1-front.webp", "WEBP")

    report = pipeline.run(ws, template)
    row = report.ready[0]
    assert [p.name for p in row.images] == ["1-front-webp.jpg", "2-back.png", "10-detail.png"]
    assert any("1-front.webp was converted" in w for w in row.warnings)
    assert (ws.drafts / report.batch / "mountain sunset shirt" / "1-front-webp.jpg").is_file()


def test_a_truncated_jpeg_is_caught_before_the_first_draft(studio):
    # Image.verify() is a no-op for JPEG — only PNG overrides it — so a photo cut short
    # by a half-finished copy passed this gate and failed on upload, after the draft.
    ws, template = studio
    folder = ws.products / "mountain sunset shirt"
    buffer = io.BytesIO()
    Image.new("RGB", (400, 400), (1, 2, 3)).save(buffer, "JPEG", quality=95)
    whole = buffer.getvalue()
    (folder / "1-front.png").unlink()
    (folder / "1-front.jpg").write_bytes(whole[: len(whole) // 2])

    client = Client(ws)
    with pytest.raises(ValidationError, match="Invalid image"):
        automation.run(ws, template, client=client)
    assert client.creates == 0


def test_image_overflow_does_not_silently_drop_photos(studio):
    ws, template = studio
    for n in range(11):
        Image.new("RGB", (20, 20)).save(ws.products / "mountain sunset shirt" / f"extra-{n}.jpg")
    client = Client(ws)
    with pytest.raises(ValidationError, match="more than 10"):
        automation.run(ws, template, client=client)
    assert client.creates == 0


def test_lock_and_corrupt_history_block_writes(studio):
    ws, template = studio
    client = Client(ws)
    lock = ws.root / ".auto-upload.lock"
    lock.write_text("another process")
    with pytest.raises(ValidationError, match="Another auto run"):
        automation.run(ws, template, client=client)
    lock.unlink()
    (ws.root / "upload-history.json").write_text("broken")
    with pytest.raises(ValidationError, match="Cannot read"):
        automation.run(ws, template, client=client)
    assert client.creates == 0


def test_invalid_template_aborts_whole_batch(studio):
    ws, template = studio
    template.fields["price"] = -1
    client = Client(ws)
    with pytest.raises(ValidationError, match="Nothing uploaded"):
        automation.run(ws, template, client=client)
    assert client.creates == 0


def test_digital_template_is_not_uploaded_without_delivery_files(studio):
    ws, template = studio
    template.fields["type"] = "download"
    with pytest.raises(ValidationError, match="digital delivery"):
        automation.run(ws, template, dry_run=True)


def _history(ws, entries):
    (ws.root / "upload-history.json").write_text(json.dumps({"123": entries}), encoding="utf-8")


def test_a_dry_run_reads_the_history_the_real_run_will_use(studio):
    # A rehearsal that ignored the history would validate products the real run skips
    # and say nothing about a half-uploaded draft needing a look.
    ws, template = studio
    _history(ws, {"mountain sunset shirt": {"status": "partial", "listing_id": 222}})
    report = automation.run(ws, template, dry_run=True)
    assert report.needs_review == ["mountain sunset shirt: partial, listing 222"]
    assert report.prepared.ready == []


def test_a_case_only_rename_is_still_the_same_product(studio):
    ws, template = studio
    _history(ws, {"Mountain Sunset Shirt": {"status": "ok", "listing_id": 900}})
    client = Client(ws)
    report = automation.run(ws, template, client=client)
    assert client.creates == 0
    assert report.already_done == ["Mountain Sunset Shirt"]


def test_each_uploaded_product_carries_its_own_review_csv_line(studio):
    ws, template = studio
    second = ws.products / "ceramic coffee mug"
    second.mkdir()
    Image.new("RGB", (20, 20), (200, 200, 200)).save(second / "01.jpg")

    class TwoProducts(Client):
        def create_draft_listing(self, fields):
            self.creates += 1
            return {"listing_id": 900 + self.creates}

        def upload_listing_image(self, listing_id, image, *, rank):
            return {}

    report = automation.run(ws, template, client=TwoProducts(ws))
    assert [r.row for r in report.uploaded.results] == [2, 3]


def test_a_create_without_a_listing_id_is_flagged_not_crashed(studio):
    ws, template = studio

    class Odd(Client):
        def create_draft_listing(self, fields):
            return "OK"

    report = automation.run(ws, template, client=Odd(ws))
    result = report.uploaded.results[0]
    assert result.status == "error"
    assert "draft may exist" in result.message
    state = json.loads((ws.root / "upload-history.json").read_text())
    assert state["123"]["mountain sunset shirt"]["status"] != "ok"


def test_review_csv_paths_stay_relative_for_ready_photos(studio):
    ws, template = studio
    report = pipeline.run(ws, template)
    text = report.csv_path.read_text(encoding="utf-8-sig")
    assert str(ws.root) not in text and ws.root.as_posix() not in text
    assert "../../2-PRODUCTS/mountain sunset shirt/1-front.png" in text


def test_the_workspace_readme_is_refreshed_when_it_is_out_of_date(tmp_path):
    ws = Workspace(tmp_path / "studio").create()
    readme = ws.root / "README.txt"
    readme.write_text("an older description", encoding="utf-8")
    ws.create()
    assert "drop auto` uploads the drafts straight away" in readme.read_text(encoding="utf-8")
