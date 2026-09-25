"""The listing every later draft copies its settings from.

Some fields simply cannot be derived from an image. `taxonomy_id`,
`shipping_profile_id`, `return_policy_id`, `who_made`, `when_made`, processing times,
the shop section, the price — these are decisions about a business, not facts about a
picture. Guessing them would put wrong listings in a real shop.

So the seller builds one listing properly in Etsy, by hand, and stallkit copies it.
That is the whole mechanism, and it is why there is no six-question wizard here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import LISTING_TYPES, MAX_QUANTITY, WHEN_MADE, WHO_MADE
from ..errors import ValidationError

# Copied verbatim onto every draft. Anything not in this list is derived per product.
INHERITED_FIELDS = (
    "taxonomy_id",
    "shipping_profile_id",
    "return_policy_id",
    "shop_section_id",
    "readiness_state_id",
    "who_made",
    "when_made",
    "type",
    "price",
    "quantity",
    "processing_min",
    "processing_max",
    "is_supply",
    "is_customizable",
    "is_taxable",
    "should_auto_renew",
    "item_weight",
    "item_weight_unit",
    "item_length",
    "item_width",
    "item_height",
    "item_dimensions_unit",
)


@dataclass
class Template:
    """Settings lifted from a real listing, plus what it teaches about copy."""

    source_listing_id: int
    source_title: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    materials: list[str] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_listing_id": self.source_listing_id,
            "source_title": self.source_title,
            "fields": self.fields,
            "materials": self.materials,
            "description": self.description,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Template:
        try:
            return cls(
                source_listing_id=int(data["source_listing_id"]),
                source_title=str(data.get("source_title", "")),
                fields=dict(data.get("fields") or {}),
                materials=list(data.get("materials") or []),
                description=str(data.get("description", "")),
                tags=list(data.get("tags") or []),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError(f"product.json is malformed: {exc}") from exc

    def missing_for_a_physical_draft(self) -> list[str]:
        """What would stop these settings producing a publishable listing."""
        gaps = []
        if not self.fields.get("taxonomy_id"):
            gaps.append("taxonomy_id")
        if self.fields.get("type", "physical") in {"physical", "both"}:
            if not self.fields.get("shipping_profile_id"):
                gaps.append("shipping_profile_id")
        if not self.fields.get("price"):
            gaps.append("price")
        return gaps

    def describe(self) -> list[tuple[str, str]]:
        """Plain-language rows for the confirmation screen."""
        f = self.fields
        rows = [
            ("Copied from", f"listing {self.source_listing_id} — {self.source_title[:60]}"),
            ("Category", str(f.get("taxonomy_id", "—"))),
            ("Shipping profile", str(f.get("shipping_profile_id", "—"))),
            ("Return policy", str(f.get("return_policy_id", "—"))),
            ("Shop section", str(f.get("shop_section_id", "—"))),
            ("Price", f"{f.get('price', '—')}"),
            ("Quantity", str(f.get("quantity", "—"))),
            ("Who made it", str(f.get("who_made", "—"))),
            ("When made", str(f.get("when_made", "—"))),
            (
                "Processing",
                f"{f.get('processing_min', '?')}–{f.get('processing_max', '?')} days",
            ),
            ("Materials", ", ".join(self.materials) or "—"),
            ("Its tags", ", ".join(self.tags) or "—"),
        ]
        return rows


def money(value: Any) -> float | None:
    if isinstance(value, dict):
        amount, divisor = value.get("amount"), value.get("divisor") or 100
        if isinstance(amount, (int, float)):
            return round(amount / divisor, 2)
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def capture(listing: dict[str, Any]) -> Template:
    """Turn an Etsy listing response into a reusable template."""
    listing_id = listing.get("listing_id")
    if not listing_id:
        raise ValidationError("That response carries no listing_id — is the id correct?")

    fields: dict[str, Any] = {}
    for name in INHERITED_FIELDS:
        if name == "price":
            price = money(listing.get("price"))
            if price is not None:
                fields["price"] = price
            continue
        if name == "type":
            value = listing.get("listing_type") or listing.get("type")
            if value in LISTING_TYPES:
                fields["type"] = value
            continue
        value = listing.get(name)
        if name == "quantity" and isinstance(value, int):
            # A varied listing reports the total across its variations; the draft only
            # needs a legal number here, and its real stock comes with the variations.
            value = min(value, MAX_QUANTITY)
        if value not in (None, ""):
            fields[name] = value

    # Etsy will refuse anything outside these, and a bad template poisons every draft.
    if fields.get("who_made") not in WHO_MADE:
        fields.pop("who_made", None)
    if fields.get("when_made") not in WHEN_MADE:
        fields.pop("when_made", None)

    return Template(
        source_listing_id=int(listing_id),
        source_title=str(listing.get("title", "")),
        fields=fields,
        materials=[str(m) for m in (listing.get("materials") or [])],
        description=str(listing.get("description", "")),
        tags=[str(t) for t in (listing.get("tags") or [])],
    )
