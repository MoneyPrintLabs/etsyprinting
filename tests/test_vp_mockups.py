"""Mockuplar and Şablon İlan as the video shows them: the strings the pages ask for exist,
and the product words the server names have Turkish and English strings.

Checked without a browser (the pages themselves are checked by hand in a browser).
"""

from __future__ import annotations

import json
import re

import pytest

from stallkit.web.api import template as template_api
from stallkit.web.server import STATIC_DIR

I18N_DIR = STATIC_DIR / "i18n"
PAGES_DIR = STATIC_DIR / "js" / "pages"

# t("key") and ctx.t("key"), but not get("/api/...").
_LITERAL_KEY = re.compile(r"""(?<![\w$.])(?:ctx\.)?t\(\s*"([a-z][a-z0-9_.:]*)"\s*[,)]""")


def _strings(name: str) -> dict:
    return json.loads((I18N_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("page", ["mockups", "template"])
def test_every_literal_key_the_page_asks_for_exists(page):
    source = (PAGES_DIR / f"{page}.js").read_text(encoding="utf-8")
    keys = set(_LITERAL_KEY.findall(source))
    assert keys, "no t(...) calls found: the pattern is out of date"
    own = _strings(page)
    common = _strings("common")
    for lang in ("tr", "en"):
        known = set(own[lang]) | set(common[lang])
        missing = sorted(k for k in keys if k not in known)
        assert not missing, f"{page}.js asks for {missing} ({lang})"


def test_the_new_strings_say_what_the_video_says():
    mockups = _strings("mockups")["tr"]
    assert mockups["library.new"] == "Yeni mockup"
    assert mockups["grid_new.sub"] == "Sürükleyip bırakın · JPG, PNG"
    assert mockups["grid_new.hint"].replace("{size}", "2000×2000") == "Önerilen: en az 2000×2000 piksel"
    assert mockups["editor.drawing"] == "Çiziliyor"
    assert mockups["editor.draw_hint"] == "Sürükleyerek baskı alanını çizin"
    assert mockups["library.same_size"] == "aynı ölçü"
    template = _strings("template")["tr"]
    assert template["footnote"] == "Yeni ilanlar bu ayarlarla oluşturulur."
    assert template["source_none"] == "Soldan bir ilan seçin"
    assert template["search"] == "İlan ara…"


def test_every_product_key_has_words_in_both_languages():
    strings = _strings("template")
    for key, _pattern in template_api._PRODUCT_WORDS:
        for lang in ("tr", "en"):
            assert strings[lang].get(f"product.{key}"), f"product.{key} ({lang})"
    # The same words as the Mockuplar page's types.
    types = _strings("mockups")
    for key, _pattern in template_api._PRODUCT_WORDS:
        for lang in ("tr", "en"):
            assert strings[lang][f"product.{key}"] == types[lang][f"type.{key}"]


@pytest.mark.parametrize(
    ("path", "key"),
    [
        (["Clothing", "Tops & Tees", "T-shirts"], "tshirt"),
        (["Clothing", "Hoodies & Sweatshirts", "Hoodies"], "hoodie"),
        (["Clothing", "Hoodies & Sweatshirts", "Sweatshirts"], "sweatshirt"),
        (["Home & Living", "Drinkware", "Mugs"], "mug"),
        (["Home & Living", "Drinkware", "Travel Mugs"], "mug"),
        (["Art & Collectibles", "Prints", "Posters"], "poster"),
        (["Art & Collectibles", "Prints", "Digital Prints"], "poster"),
        (["Art & Collectibles", "Prints", "Giclee"], "poster"),  # under Prints
        (["Art & Collectibles", "Painting", "Canvas"], "canvas"),
        (["Electronics & Accessories", "Phone Accessories", "Phone Cases"], "phone_case"),
        (["Bags & Purses", "Totes"], "tote"),
        (["Home & Living", "Home Decor", "Pillows"], "pillow"),
        (["Paper & Party Supplies", "Stickers, Labels & Tags", "Stickers"], "sticker"),
        (["Jewelry", "Rings"], None),
        (["Prints"], "poster"),
        ([], None),
    ],
)
def test_product_key_names_the_product(path, key):
    assert template_api._product_key(path) == key


# ---------------------------------------------------------------- font weights

CSS_DIR = STATIC_DIR / "css" / "pages"


def _css(name: str) -> str:
    return (CSS_DIR / f"{name}.css").read_text(encoding="utf-8")


def _weight(css: str, selector: str) -> str:
    """The font-weight of the first rule written for exactly this selector."""
    match = re.search(r"(?:^|\n)" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"no rule for {selector}"
    weight = re.search(r"font-weight:\s*(\d+)", match.group(1))
    assert weight, f"{selector} sets no font-weight"
    return weight.group(1)


@pytest.mark.parametrize("page", ["mockups", "template"])
def test_the_pages_use_only_the_weights_the_video_uses(page):
    # 550 and 650 exist nowhere in the video; with its static fonts they snap one step.
    weights = set(re.findall(r"font-weight:\s*(\d+)", _css(page)))
    assert weights <= {"400", "500", "600", "700"}, sorted(weights)


@pytest.mark.parametrize(
    ("page", "selector", "weight"),
    [
        # MockuplarEkrani.tsx / primitives.tsx
        ("mockups", ".page-mockups .mk-field-input .input", "400"),  # Input value: no weight
        ("mockups", ".page-mockups .mk-field .field-label", "600"),  # Input label
        ("mockups", ".page-mockups .mk-zoom", "400"),  # "%100": mono, no weight
        ("mockups", ".page-mockups .mk-lib-title", "700"),  # "Mockup kütüphanesi"
        ("mockups", ".page-mockups .mk-side-title", "700"),  # editor title
        ("mockups", ".page-mockups .mk-lib-applied", "600"),  # "uygulandı"
        ("mockups", ".page-mockups .mk-lib-new-sub", "400"),  # "· JPG, PNG"
        ("mockups", ".page-mockups .mk-applied-title", "600"),  # "Baskı alanı N mockup'a uygulandı"
        ("mockups", ".page-mockups .mk-state", "500"),  # "baskı alanı ayarlı"
        ("mockups", ".page-mockups .mk-toolbar .chip-count", "400"),  # inactive chip count
        ("mockups", ".page-mockups .mk-toolbar .chip.is-active .chip-count", "600"),
        # SablonEkrani.tsx
        ("template", ".tpl-state", "400"),  # "Aktif"
        ("template", ".tpl-more-btn", "600"),  # "N ilan daha göster"
        ("template", ".tpl-value.mono", "600"),  # copied price
        ("template", ".tpl-bottom .note-text b", "600"),  # "Bunlar ticari kararlar."
    ],
)
def test_the_weights_match_the_video(page, selector, weight):
    assert _weight(_css(page), selector) == weight
