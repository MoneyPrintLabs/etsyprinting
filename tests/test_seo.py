from stallkit.seo import Issue, MarketReport, audit_listing, content_words, ngrams, suggest_tags


def make_listing(**overrides):
    listing = {
        "listing_id": 1,
        "title": "Handmade Ceramic Coffee Mug Minimalist Stoneware Cup Gift for Coffee Lover",
        "description": "A wheel-thrown ceramic coffee mug in matte cream glaze. " * 4,
        "tags": [f"tag {i}" for i in range(13)],
        "materials": ["stoneware"],
        "should_auto_renew": True,
        "state": "active",
    }
    listing.update(overrides)
    return listing


def codes(listing):
    return {i.code for i in audit_listing(listing).issues}


def test_clean_listing_scores_well():
    listing = make_listing(tags=["ceramic mug"] + [f"coffee gift {i}" for i in range(12)])
    audit = audit_listing(listing)
    assert audit.score >= 60


def test_empty_tags_is_an_error():
    assert "tags.missing" in codes(make_listing(tags=[]))


def test_unused_tag_slots_flagged():
    assert "tags.unused" in codes(make_listing(tags=["mug", "cup"]))


def test_duplicate_tags_flagged():
    assert "tags.duplicate" in codes(make_listing(tags=["mug"] * 13))


def test_near_duplicate_tags_flagged():
    issues = codes(make_listing(tags=["gift", "gifts"] + [f"other {i}" for i in range(11)]))
    assert "tags.near_duplicate" in issues


def test_long_tag_is_an_error():
    assert "tags.too_long" in codes(make_listing(tags=["a" * 21] + ["b c"] * 12))


def test_short_title_flagged():
    assert "title.too_short" in codes(make_listing(title="Mug"))


def test_over_length_title_is_an_error():
    assert "title.too_long" in codes(make_listing(title="x" * 141))


def test_repeated_word_in_title_flagged():
    assert "title.repetition" in codes(make_listing(title="Mug mug mug ceramic coffee cup gift set"))


def test_thin_description_flagged():
    assert "description.thin" in codes(make_listing(description="Short."))


def test_expired_listing_flagged():
    assert "state.expired" in codes(make_listing(state="expired"))


def test_empty_listing_is_graded_poor():
    audit = audit_listing({"listing_id": 2, "title": "", "description": "", "tags": []})
    assert {"title.missing", "tags.missing", "description.missing"} <= codes(
        {"listing_id": 2, "title": "", "description": "", "tags": []}
    )
    assert audit.grade == "poor"


def test_score_is_clamped_at_zero():
    audit = audit_listing({"listing_id": 3, "title": "", "description": "", "tags": []})
    audit.issues.extend(Issue("x", "error", "synthetic") for _ in range(10))
    assert audit.score == 0


def test_content_words_drops_stopwords_and_keeps_unicode():
    assert content_words("Gift for the Çiçek düğme") == ["gift", "çiçek", "düğme"]


def test_ngrams_skip_windows_containing_stopwords():
    assert list(ngrams(["gift", "for", "her"], 2)) == []
    assert list(ngrams(["ceramic", "coffee", "mug"], 2)) == ["ceramic coffee", "coffee mug"]


def test_suggest_tags_excludes_what_you_already_have():
    report = MarketReport(
        keyword="mug", sampled=100,
        tags=[("ceramic mug", 60), ("coffee gift", 40), ("stoneware", 30)],
        phrases=[], price_min=None, price_median=None, price_max=None,
        currency="USD", median_favorers=None, top_listings=[],
    )
    assert suggest_tags(report, existing=["Ceramic Mug"]).add_now == ["coffee gift", "stoneware"]
