import pytest

from stallkit.client import encode_form
from stallkit.csvio import as_float, split_multi
from stallkit.errors import ValidationError
from stallkit.listings import bad_tag_chars, build_payload, prepare, validate_tags

BASE_ROW = {
    "title": "Handmade Ceramic Mug",
    "description": "A nice mug.",
    "price": "24.00",
    "quantity": "5",
    "who_made": "i_did",
    "when_made": "made_to_order",
    "taxonomy_id": "1633",
    "shipping_profile_id": "12345",
}


def test_minimal_create_row_builds():
    payload = build_payload(dict(BASE_ROW), is_update=False)
    assert payload["title"] == "Handmade Ceramic Mug"
    assert payload["price"] == 24.0
    assert payload["quantity"] == 5
    assert payload["type"] == "physical"


def test_missing_shipping_profile_warns_but_does_not_block_a_draft():
    # stallkit only creates drafts, and Etsy does not require a shipping profile on a
    # draft. Blocking here would stop a new seller from staging anything at all.
    row = dict(BASE_ROW)
    del row["shipping_profile_id"]
    warnings: list[str] = []
    payload = build_payload(row, is_update=False, warnings=warnings)
    assert payload["title"]
    assert any("shipping_profile_id" in w for w in warnings)


def test_digital_listing_does_not_warn_about_shipping():
    row = dict(BASE_ROW)
    del row["shipping_profile_id"]
    row["type"] = "download"
    warnings: list[str] = []
    assert build_payload(row, is_update=False, warnings=warnings)["type"] == "download"
    assert warnings == []


def test_title_over_limit_rejected():
    row = dict(BASE_ROW, title="x" * 141)
    with pytest.raises(ValidationError, match="141 chars"):
        build_payload(row, is_update=False)


def test_bad_enum_rejected():
    row = dict(BASE_ROW, who_made="me")
    with pytest.raises(ValidationError, match="who_made"):
        build_payload(row, is_update=False)


def test_decimal_comma_price_accepted():
    row = dict(BASE_ROW, price="19,90")
    assert build_payload(row, is_update=False)["price"] == pytest.approx(19.90)


def test_update_row_only_keeps_updatable_fields():
    # price and quantity are deliberately not patchable — they belong to inventory.
    payload = build_payload({"title": "New title", "price": "9.99"}, is_update=True)
    assert payload == {"title": "New title"}


def test_update_requires_at_least_one_field():
    with pytest.raises(ValidationError, match="no updatable fields"):
        build_payload({"listing_id": "1"}, is_update=True)


def test_state_only_on_update():
    row = dict(BASE_ROW, state="active")
    with pytest.raises(ValidationError, match="state can only be set"):
        build_payload(row, is_update=False)


def test_tags_are_split_and_kept():
    row = dict(BASE_ROW, tags="ceramic mug|coffee gift|stoneware")
    assert build_payload(row, is_update=False)["tags"] == ["ceramic mug", "coffee gift", "stoneware"]


def test_too_many_tags_rejected():
    row = dict(BASE_ROW, tags="|".join(f"tag{i}" for i in range(14)))
    with pytest.raises(ValidationError, match="Etsy allows 13"):
        build_payload(row, is_update=False)


def test_tag_over_20_chars_rejected():
    row = dict(BASE_ROW, tags="this tag is far too long to be accepted")
    with pytest.raises(ValidationError, match="max 20"):
        build_payload(row, is_update=False)


def test_unicode_tags_are_allowed():
    # Turkish, German and French letters must survive — str.isalnum is Unicode-aware.
    assert bad_tag_chars("çiçek düğme") == set()
    assert bad_tag_chars("café münze") == set()
    assert validate_tags(["çiçek", "düğme hediyesi"]) == []


def test_disallowed_tag_symbol_flagged():
    assert bad_tag_chars("mug!") == {"!"}
    assert validate_tags(["mug!"])


def test_duplicate_tags_flagged():
    assert any("duplicate" in p for p in validate_tags(["mug", "Mug"]))


def test_more_images_than_etsy_allows_is_an_error_not_a_truncation(tmp_path):
    # Etsy takes the create and then refuses the 21st upload, and stallkit holds no
    # delete scope to undo it. Dropping the extras silently would pick which photos the
    # seller ships; failing the row leaves that choice with them.
    for n in range(21):
        (tmp_path / f"{n}.jpg").write_bytes(b"x")
    row = dict(BASE_ROW, images="|".join(f"{n}.jpg" for n in range(21)))

    result = prepare([row], base_dir=tmp_path)[0].result

    assert result.failed
    assert "21 images given" in result.message
    assert "Etsy allows 20" in result.message


def test_exactly_ten_images_is_a_valid_row(tmp_path):
    # Ten is a full listing, not an error — the boundary has to stay usable.
    for n in range(10):
        (tmp_path / f"{n}.jpg").write_bytes(b"x")
    row = dict(BASE_ROW, images="|".join(f"{n}.jpg" for n in range(10)))

    assert not prepare([row], base_dir=tmp_path)[0].result.failed


def test_image_count_is_not_policed_when_uploads_are_off(tmp_path):
    # --no-images sends nothing, so an eleventh path cannot reach Etsy. Failing the row
    # here would stop a seller fixing their titles over a column this run ignores.
    row = dict(BASE_ROW, images="|".join(f"{n}.jpg" for n in range(11)))

    assert not prepare([row], base_dir=tmp_path, upload_images=False)[0].result.failed


def test_encode_form_joins_lists_with_commas():
    # Etsy documents tags as a comma-separated string; repeated keys silently lose data.
    encoded = encode_form({"tags": ["a", "b", "c"], "quantity": 3, "is_supply": False})
    assert encoded == {"tags": "a,b,c", "quantity": "3", "is_supply": "false"}


def test_encode_form_drops_none_and_empty_lists():
    assert encode_form({"a": None, "b": [], "c": 1}) == {"c": "1"}


def test_a_thousands_separator_is_refused_rather_than_read_as_a_decimal_point():
    # Read as 1.299, '1,299' would list a 1.299 TL poster for one lira thirty, and
    # `price > 0` would wave it through. Nothing about its shape distinguishes it from
    # '19,90', so the ambiguous one has to be an error rather than a guess.
    assert as_float("19,90", "price") == 19.90
    assert as_float("19.90", "price") == 19.90
    assert as_float("1299", "price") == 1299.0
    with pytest.raises(ValidationError) as caught:
        as_float("1,299", "price")
    assert "1299" in str(caught.value)


def test_a_comma_in_an_image_path_does_not_split_the_cell():
    # A one-image row has no pipe, so a comma fallback would tear a perfectly good
    # path in two and push would report both halves as missing files.
    assert split_multi("kedi, kopek tablosu--flat.jpg") == ["kedi, kopek tablosu--flat.jpg"]
    assert split_multi("a.jpg|b.jpg") == ["a.jpg", "b.jpg"]


def test_a_processing_profile_is_sent_and_replaces_the_day_counts():
    # Etsy refuses a physical create without readiness_state_id, and the profile is what
    # defines processing time — the older day counts must not contradict it.
    row = dict(BASE_ROW, readiness_state_id="9001", processing_min="1", processing_max="2")
    payload = build_payload(row, is_update=False)
    assert payload["readiness_state_id"] == 9001
    assert "processing_min" not in payload and "processing_max" not in payload


def test_day_counts_still_go_out_without_a_profile():
    payload = build_payload(dict(BASE_ROW, processing_min="1", processing_max="2"), is_update=False)
    assert payload["processing_min"] == 1 and payload["processing_max"] == 2


def test_a_quantity_over_etsys_limit_is_caught_before_sending():
    problems = []
    try:
        build_payload(dict(BASE_ROW, quantity="12000"), is_update=False)
    except ValidationError as exc:
        problems.append(str(exc))
    assert problems and "limit of 999" in problems[0]
