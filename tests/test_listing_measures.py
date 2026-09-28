"""Weights and sizes Etsy would refuse are left out, not sent (createDraftListing: "If
set, the value must be greater than 0", and each comes with its unit)."""

from __future__ import annotations

from stallkit import listings
from stallkit.drop import template as template_mod

BASE = {
    "title": "Retro Sunset Shirt",
    "description": "A shirt.",
    "price": "19.99",
    "quantity": "5",
    "who_made": "i_did",
    "when_made": "made_to_order",
    "taxonomy_id": "482",
    "shipping_profile_id": "111",
}


def _payload(**extra):
    warnings: list[str] = []
    payload = listings.build_payload({**BASE, **extra}, is_update=False, warnings=warnings)
    return payload, warnings


def test_a_zero_weight_is_not_sent_and_the_seller_is_told():
    payload, warnings = _payload(item_weight="0", item_weight_unit="oz")
    assert "item_weight" not in payload and "item_weight_unit" not in payload
    assert any("item_weight 0 not sent" in w for w in warnings)


def test_a_zero_weight_from_a_saved_template_is_not_sent():
    payload, _ = _payload(item_weight="0.0", item_weight_unit="g")
    assert "item_weight" not in payload


def test_a_real_weight_with_its_unit_is_sent():
    payload, warnings = _payload(item_weight="250", item_weight_unit="g")
    assert payload["item_weight"] == 250.0 and payload["item_weight_unit"] == "g"
    assert not any("item_weight" in w for w in warnings)


def test_a_weight_without_a_unit_is_left_out():
    payload, warnings = _payload(item_weight="12")
    assert "item_weight" not in payload
    assert any("item_weight_unit is empty" in w for w in warnings)


def test_a_unit_without_a_weight_is_left_out():
    payload, _ = _payload(item_weight_unit="oz")
    assert "item_weight_unit" not in payload


def test_sizes_follow_the_same_rules():
    payload, _ = _payload(item_length="30", item_width="0", item_height="2", item_dimensions_unit="cm")
    assert payload["item_length"] == 30.0 and payload["item_height"] == 2.0
    assert "item_width" not in payload and payload["item_dimensions_unit"] == "cm"

    payload, warnings = _payload(item_length="30", item_width="20")
    assert "item_length" not in payload and "item_width" not in payload
    assert any("item_dimensions_unit is empty" in w for w in warnings)

    payload, _ = _payload(item_length="0", item_dimensions_unit="in")
    assert "item_length" not in payload and "item_dimensions_unit" not in payload


def test_capture_does_not_copy_a_weight_nobody_entered():
    listing = {
        "listing_id": 42, "title": "Poster", "price": {"amount": 1500, "divisor": 100},
        "quantity": 3, "who_made": "i_did", "when_made": "2020_2026", "taxonomy_id": 1,
        "shipping_profile_id": 7, "listing_type": "physical",
        "item_weight": 0, "item_weight_unit": "oz",
        "item_length": 0.0, "item_width": 0, "item_height": 0, "item_dimensions_unit": "in",
    }
    fields = template_mod.capture(listing).fields
    for name in ("item_weight", "item_weight_unit", "item_length", "item_width",
                 "item_height", "item_dimensions_unit"):
        assert name not in fields


def test_capture_keeps_a_real_weight():
    listing = {
        "listing_id": 43, "title": "Mug", "price": {"amount": 1500, "divisor": 100},
        "quantity": 3, "who_made": "i_did", "when_made": "2020_2026", "taxonomy_id": 1,
        "shipping_profile_id": 7, "listing_type": "physical",
        "item_weight": 340, "item_weight_unit": "g",
    }
    fields = template_mod.capture(listing).fields
    assert fields["item_weight"] == 340 and fields["item_weight_unit"] == "g"
