"""The mockup catalog: type/colour/enabled per mockup, and which print area applies."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from stallkit.drop import catalog, mockup
from stallkit.drop.workspace import Workspace
from stallkit.errors import ValidationError


def image_bytes(size=(40, 30), fmt="PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 200, 200)).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def ws(tmp_path) -> Workspace:
    workspace = Workspace(tmp_path / "Etsy Studio").create()
    for name, size in (("a-shirt-white.png", (40, 30)), ("b-shirt-black.png", (40, 30)),
                       ("c-mug.png", (50, 50))):
        (workspace.mockups / name).write_bytes(image_bytes(size))
    return workspace


@pytest.mark.parametrize("filename, expected", [
    ("tshirt-white.jpg", ("tshirt", "Beyaz")),
    ("T-Shirt Black 01.png", ("tshirt", "Siyah")),
    ("tişört-lacivert.jpg", ("tshirt", "Lacivert")),
    ("KUPA_beyaz.png", ("mug", "Beyaz")),
    ("coffee-mug.png", ("mug", "")),
    ("hoodie-grey.jpg", ("hoodie", "Gri")),
    ("kapüşonlu-siyah.jpg", ("hoodie", "Siyah")),
    ("sweatshirt-cream.jpg", ("sweatshirt", "Krem")),
    ("poster-oak-frame.jpg", ("poster", "Meşe")),
    ("çerçeve meşe.jpg", ("poster", "Meşe")),
    ("canvas-print.jpg", ("canvas", "")),
    ("iphone-case.png", ("phone_case", "")),
    ("telefon-kılıf-pembe.png", ("phone_case", "Pembe")),
    ("tote-bag-natural.png", ("tote", "Krem")),
    ("çanta.png", ("tote", "")),
    ("yastık-kırmızı.png", ("pillow", "Kırmızı")),
    ("sticker.png", ("sticker", "")),
    ("IMG_0001.jpg", ("other", "")),
])
def test_guess_reads_english_and_turkish_file_names(filename, expected):
    assert catalog.guess(filename) == expected


def test_load_guesses_what_has_no_entry_and_ignores_orphans(ws):
    catalog.catalog_path(ws).write_text(json.dumps({
        "c-mug.png": {"type": "mug", "color": "Krem", "enabled": False},
        "gone.png": {"type": "poster", "color": "", "enabled": True},
    }), encoding="utf-8")
    infos = catalog.load(ws)
    assert list(infos) == ["a-shirt-white.png", "b-shirt-black.png", "c-mug.png"]
    assert infos["a-shirt-white.png"].to_dict() == {
        "name": "a-shirt-white.png", "type": "tshirt", "color": "Beyaz", "enabled": True}
    assert infos["c-mug.png"].enabled is False and infos["c-mug.png"].color == "Krem"


def test_a_broken_catalog_file_is_not_fatal(ws):
    catalog.catalog_path(ws).write_text("{nope", encoding="utf-8")
    assert len(catalog.load(ws)) == 3


def test_update_records_one_mockup(ws):
    info = catalog.update(ws, "c-mug.png", type="mug", color="Siyah", enabled=False)
    assert (info.type, info.color, info.enabled) == ("mug", "Siyah", False)
    assert catalog.load(ws)["c-mug.png"].enabled is False
    with pytest.raises(ValidationError):
        catalog.update(ws, "c-mug.png", type="spaceship")
    with pytest.raises(FileNotFoundError):
        catalog.update(ws, "../product.json", enabled=True)
    with pytest.raises(FileNotFoundError):
        catalog.update(ws, "missing.png", enabled=True)


def test_enabled_mockups_skip_disabled_ones_and_stop_at_19(ws):
    catalog.update(ws, "b-shirt-black.png", enabled=False)
    assert [p.name for p in catalog.enabled_mockups(ws)] == ["a-shirt-white.png", "c-mug.png"]
    for n in range(25):
        (ws.mockups / f"z-{n:02d}.png").write_bytes(image_bytes())
    assert len(catalog.enabled_mockups(ws)) == catalog.MAX_ENABLED


def test_add_saves_a_safe_name_and_never_overwrites(ws):
    assert catalog.add(ws, "new shirt.PNG", image_bytes()) == "new shirt.png"
    assert catalog.add(ws, "new shirt.png", image_bytes()) == "new shirt-2.png"
    assert catalog.add(ws, "..\\..\\evil.png", image_bytes()) == "evil.png"
    assert catalog.add(ws, "CON.png", image_bytes()) == "mockup-CON.png"
    assert (ws.mockups / "new shirt-2.png").is_file()
    with pytest.raises(ValidationError):
        catalog.add(ws, "notes.txt", b"hello")
    with pytest.raises(ValidationError):
        catalog.add(ws, "fake.png", b"not an image at all")
    with pytest.raises(ValidationError):
        catalog.add(ws, "empty.png", b"")


def test_remove_deletes_the_file_and_what_is_recorded_about_it(ws):
    catalog.update(ws, "c-mug.png", color="Krem")
    mockup.save_positions(ws.positions_path, {
        "c-mug.png": mockup.PrintArea(0.1, 0.1, 0.5, 0.5),
        "a-shirt-white.png": mockup.PrintArea(0.2, 0.2, 0.4, 0.4),
    })
    catalog.remove(ws, "c-mug.png")
    assert not (ws.mockups / "c-mug.png").exists()
    assert "c-mug.png" not in json.loads(catalog.catalog_path(ws).read_text(encoding="utf-8"))
    assert set(mockup.load_positions(ws.positions_path)) == {"a-shirt-white.png"}
    with pytest.raises(FileNotFoundError):
        catalog.remove(ws, "c-mug.png")


def test_effective_area_matches_the_pipeline_lookup(ws):
    assert catalog.effective_area(ws, "a-shirt-white.png") == (
        mockup.DEFAULT_PRINT_AREA, catalog.SOURCE_DEFAULT)
    own = mockup.PrintArea(0.2, 0.2, 0.4, 0.4)
    mockup.save_positions(ws.positions_path, {"a-shirt-white.png": own})
    areas = catalog.effective_areas(ws)
    assert areas["a-shirt-white.png"] == (own, catalog.SOURCE_OWN)
    assert areas["b-shirt-black.png"] == (own, catalog.SOURCE_SAME_SIZE)  # same 40x30
    assert areas["c-mug.png"] == (mockup.DEFAULT_PRINT_AREA, catalog.SOURCE_DEFAULT)


def test_same_size_names_and_save_area(ws):
    assert catalog.same_size_names(ws, "a-shirt-white.png") == ["a-shirt-white.png", "b-shirt-black.png"]
    assert catalog.same_size_names(ws, "missing.png") == []
    area = mockup.PrintArea(0.1, 0.2, 0.3, 0.4)
    assert catalog.save_area(ws, "a-shirt-white.png", area, same_size=True, dry_run=True) == [
        "a-shirt-white.png", "b-shirt-black.png"]
    assert not ws.positions_path.exists()
    assert catalog.save_area(ws, "c-mug.png", area) == ["c-mug.png"]
    assert set(mockup.load_positions(ws.positions_path)) == {"c-mug.png"}
    catalog.save_area(ws, "a-shirt-white.png", area, same_size=True)
    assert set(mockup.load_positions(ws.positions_path)) == {
        "a-shirt-white.png", "b-shirt-black.png", "c-mug.png"}
    assert catalog.clear_area(ws, "c-mug.png") is True
    assert catalog.clear_area(ws, "c-mug.png") is False
