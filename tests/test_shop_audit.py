"""Problems that only exist between listings.

`audit_listing` judges one listing at a time, which makes a whole class of problem
invisible: a shop where every listing carries the same tags scores a perfect 100 on
every listing while they all compete with each other for the same query. These tests
pin the check that catches it — it was found by hand on a real shop that the
per-listing audit had just declared flawless.
"""

import pytest

from stallkit.seo import audit_listing, audit_shop, overlapping_pairs


def _listing(listing_id, tags, title="Soy Candle"):
    return {"listing_id": listing_id, "title": title, "tags": tags, "description": "x" * 300}


# Deliberately generic fixture vocabulary.
SHARED = ["soy wax candle", "hand poured candle", "scented candle", "gift for her",
          "home fragrance", "candle gift set", "vegan candle"]


def _shop(count, distinctive_each):
    return [
        _listing(i, SHARED + [f"unique {i}-{n}" for n in range(distinctive_each)])
        for i in range(count)
    ]


def test_a_shop_where_every_listing_says_the_same_thing_is_flagged():
    listings = [_listing(i, SHARED) for i in range(10)]
    shop = audit_shop(listings)
    codes = {i.code for i in shop.issues}
    assert "shop.tag_overlap" in codes
    assert "shop.too_alike" in codes


def test_the_per_listing_audit_still_calls_those_listings_perfect():
    # This is the blind spot, stated as a test so it cannot quietly come back:
    # a listing can be flawless on its own terms while the shop it sits in is not.
    title = "Soy Wax Candle Gift Set | Hand Poured Lavender Scented Vegan Candle"
    listing = {
        "listing_id": 1,
        "title": title,
        "description": (
            f"{title}. A soy wax candle gift set, hand poured with lavender scent, "
            "a vegan candle made for home fragrance and given as a gift. "
        ) * 3,
        "tags": [
            "soy wax candle", "hand poured candle", "scented candle", "lavender candle",
            "vegan candle", "candle gift set", "soy candle gift", "poured candle",
            "lavender scented", "candle set", "wax candle gift", "hand poured wax",
            "lavender gift",
        ],
        "materials": ["soy wax"],
        "should_auto_renew": True,
        "state": "active",
    }
    audit = audit_listing(listing)
    assert audit.score == 100, audit.summary()

    shop = audit_shop([dict(listing, listing_id=i) for i in range(10)])
    assert shop.issues, "the shop-level check must see what the listing-level one cannot"
    assert "shop.tag_overlap" in {i.code for i in shop.issues}


def test_a_varied_shop_raises_nothing():
    listings = [_listing(i, [f"tag {i}-{n}" for n in range(13)]) for i in range(10)]
    assert audit_shop(listings).issues == []


def test_shop_wide_tags_are_counted_and_ordered():
    listings = _shop(10, distinctive_each=6)
    shop = audit_shop(listings)
    names = [tag for tag, _n in shop.shop_wide]
    assert set(names) == set(SHARED)
    assert shop.shop_wide[0][1] == 10


def test_distinctive_counts_exclude_the_shared_tags():
    shop = audit_shop(_shop(10, distinctive_each=6))
    assert set(shop.distinctive_per_listing.values()) == {6}
    assert shop.median_distinctive == 6


def test_listings_with_too_little_of_their_own_are_named():
    listings = _shop(6, distinctive_each=6) + [_listing(99, SHARED + ["only one"])]
    shop = audit_shop(listings)
    crowded_ids = {row[0] for row in shop.crowded}
    assert 99 in crowded_ids
    assert all(row[0] == 99 for row in shop.crowded), "the varied listings are fine"


def test_the_worst_listing_sorts_first():
    listings = _shop(6, distinctive_each=6) + [
        _listing(98, SHARED + ["a", "b", "c"]), _listing(99, SHARED)
    ]
    crowded = audit_shop(listings).crowded
    assert crowded[0][0] == 99 and crowded[0][2] == 0


def test_a_single_listing_shop_cannot_cannibalise_itself():
    assert audit_shop([_listing(1, SHARED)]).issues == []
    assert audit_shop([]).issues == []


# --- the competing pairs --------------------------------------------------------


def test_identical_listings_are_reported_as_fully_overlapping():
    pairs = overlapping_pairs([_listing(1, SHARED, "A"), _listing(2, SHARED, "B")])
    assert pairs and pairs[0][2] == pytest.approx(1.0)


def test_unrelated_listings_are_not_reported():
    listings = [_listing(1, ["a", "b", "c"], "A"), _listing(2, ["x", "y", "z"], "B")]
    assert overlapping_pairs(listings) == []


def test_pairs_come_back_worst_first():
    listings = [
        _listing(1, SHARED, "A"),
        _listing(2, SHARED, "B"),
        _listing(3, SHARED[:4] + ["p", "q", "r"], "C"),
    ]
    pairs = overlapping_pairs(listings, threshold=0.3)
    assert pairs[0][2] >= pairs[-1][2]


def test_the_pair_list_is_capped():
    listings = [_listing(i, SHARED, f"L{i}") for i in range(8)]
    assert len(overlapping_pairs(listings, limit=3)) == 3
