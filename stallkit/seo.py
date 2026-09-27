"""SEO analysis.

Two independent things live here:

*Audit* looks only at your own listings and checks them against Etsy's documented
limits plus the mechanics of how Etsy search reads a listing — unused tag slots,
titles that bury the keyword past the truncation point, tags that no longer appear
anywhere in the title.

*Keyword research* reads the public marketplace through findAllListingsActive (the
one endpoint that needs no OAuth token) and reports what the listings ranking for a
term actually have in common: their tags, their title phrases, their price band.

Neither invents search-volume numbers. Etsy does not expose them, and any tool
claiming otherwise is guessing. What you get here is measured from real listings.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .client import EtsyClient
from .config import MAX_TAG_LEN, MAX_TAGS, MAX_TITLE_LEN

# Etsy truncates titles in search results and on mobile cards around here, so the
# keyword a buyer scans for should land inside this window.
TITLE_VISIBLE_CHARS = 40
TITLE_MIN_USEFUL = 40
DESCRIPTION_MIN_USEFUL = 160

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "that", "the", "this", "to", "with", "your", "you", "my", "our",
    "can", "will", "not", "but", "all", "any", "have", "has", "was", "were", "into",
    "ve", "ile", "bir", "bu", "icin", "için", "da", "de", "en",
}

_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?|\d+", re.UNICODE)

SEVERITY_WEIGHT = {"error": 25, "warn": 10, "info": 3}


@dataclass
class Issue:
    code: str
    severity: str  # error | warn | info
    message: str


@dataclass
class Audit:
    listing_id: int
    title: str
    url: str = ""
    issues: list[Issue] = field(default_factory=list)

    @property
    def score(self) -> int:
        penalty = sum(SEVERITY_WEIGHT.get(i.severity, 5) for i in self.issues)
        return max(0, 100 - penalty)

    @property
    def grade(self) -> str:
        score = self.score
        if score >= 85:
            return "good"
        if score >= 60:
            return "fair"
        return "poor"

    def summary(self) -> str:
        return "; ".join(i.message for i in self.issues) or "no issues found"


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def content_words(text: str) -> list[str]:
    return [w for w in words(text) if w not in STOPWORDS and len(w) > 2]


def audit_listing(listing: dict[str, Any]) -> Audit:
    title = (listing.get("title") or "").strip()
    description = (listing.get("description") or "").strip()
    tags = [t for t in (listing.get("tags") or []) if t]
    materials = listing.get("materials") or []

    result = Audit(
        listing_id=int(listing.get("listing_id") or 0),
        title=title,
        url=listing.get("url", ""),
    )
    add = result.issues.append

    # --- title -----------------------------------------------------------------
    if not title:
        add(Issue("title.missing", "error", "title is empty"))
    else:
        if len(title) > MAX_TITLE_LEN:
            add(Issue("title.too_long", "error",
                      f"title is {len(title)} chars, Etsy caps at {MAX_TITLE_LEN}"))
        elif len(title) < TITLE_MIN_USEFUL:
            add(Issue("title.too_short", "warn",
                      f"title is only {len(title)} chars — short titles cover fewer queries"))

        head_words = set(content_words(title[:TITLE_VISIBLE_CHARS]))
        tail_words = set(content_words(title[TITLE_VISIBLE_CHARS:]))
        if tail_words and not head_words:
            add(Issue("title.front_empty", "warn",
                      f"the first {TITLE_VISIBLE_CHARS} chars carry no keyword — that is all "
                      "a buyer sees before the title is truncated"))

        counts = Counter(content_words(title))
        stuffed = [w for w, c in counts.items() if c >= 3]
        if stuffed:
            add(Issue("title.repetition", "warn",
                      f"repeated {'word' if len(stuffed) == 1 else 'words'} in title: "
                      f"{', '.join(sorted(stuffed))}"))

        if title.count(",") > 6:
            add(Issue("title.comma_spam", "info",
                      f"{title.count(',')} commas — long keyword chains read as spam to buyers"))

        shouty = [w for w in re.findall(r"\b[A-ZÇĞİÖŞÜ]{4,}\b", title) if w.isupper()]
        if len(shouty) >= 2:
            add(Issue("title.caps", "info", f"ALL-CAPS words in title: {', '.join(shouty[:3])}"))

    # --- tags ------------------------------------------------------------------
    if not tags:
        add(Issue("tags.missing", "error", "no tags — Etsy gives you 13 free query slots"))
    else:
        if len(tags) > MAX_TAGS:
            # The listing validator enforces this too; without it here, a listing
            # carrying more tags than Etsy allows could score a clean 100.
            add(Issue("tags.too_many", "error",
                      f"{len(tags)} tags — Etsy allows {MAX_TAGS}, the rest are ignored"))
        elif len(tags) < MAX_TAGS:
            add(Issue("tags.unused", "warn",
                      f"{len(tags)}/{MAX_TAGS} tags used — {MAX_TAGS - len(tags)} slot(s) left on the table"))

        too_long = [t for t in tags if len(t) > MAX_TAG_LEN]
        if too_long:
            add(Issue("tags.too_long", "error",
                      f"tag(s) over {MAX_TAG_LEN} chars: {', '.join(too_long[:3])}"))

        lowered = [t.lower().strip() for t in tags]
        dupes = {t for t in lowered if lowered.count(t) > 1}
        if dupes:
            add(Issue("tags.duplicate", "error", f"duplicate tags: {', '.join(sorted(dupes))}"))

        near = _near_duplicates(lowered)
        if near:
            add(Issue("tags.near_duplicate", "info",
                      "near-duplicate tags compete with each other: "
                      + ", ".join(f"{a}/{b}" for a, b in near[:3])))

        single = [t for t in tags if len(t.split()) == 1]
        if tags and len(single) / len(tags) > 0.6:
            add(Issue("tags.single_word", "warn",
                      f"{len(single)}/{len(tags)} tags are single words — multi-word "
                      "long-tail tags face far less competition"))

        title_words = set(content_words(title))
        orphans = [t for t in tags if not (set(content_words(t)) & title_words)]
        if title and len(orphans) > len(tags) / 2:
            add(Issue("tags.title_mismatch", "warn",
                      f"{len(orphans)} tag(s) share no word with the title — Etsy ranks "
                      "listings higher when title and tags reinforce each other"))

    # --- description -----------------------------------------------------------
    if not description:
        add(Issue("description.missing", "error", "description is empty"))
    elif len(description) < DESCRIPTION_MIN_USEFUL:
        add(Issue("description.thin", "warn",
                  f"description is {len(description)} chars — thin descriptions convert poorly"))
    else:
        opening = set(content_words(description[:DESCRIPTION_MIN_USEFUL]))
        if title and not (opening & set(content_words(title))):
            add(Issue("description.opening", "info",
                      "the opening lines repeat none of the title keywords — that snippet is "
                      "what Google shows"))

    # --- everything else --------------------------------------------------------
    if not materials:
        add(Issue("materials.missing", "info", "no materials set — a free, indexed attribute"))
    if listing.get("should_auto_renew") is False:
        add(Issue("renew.off", "info", "auto-renew is off; the listing will expire in 4 months"))
    if listing.get("state") == "expired":
        add(Issue("state.expired", "warn", "listing has expired and is not visible"))

    return result


def _near_duplicates(tags: Sequence[str]) -> list[tuple[str, str]]:
    """Catch pairs like 'gift'/'gifts' that burn two slots on one query."""
    pairs = []
    for i, a in enumerate(tags):
        for b in tags[i + 1:]:
            if a == b:
                continue
            if a.rstrip("s") == b.rstrip("s") or a.replace(" ", "") == b.replace(" ", ""):
                pairs.append((a, b))
    return pairs


def audit_all(client: EtsyClient, *, state: str = "active", max_items: int | None = None) -> list[Audit]:
    return [audit_listing(listing) for listing in client.listings_by_shop(state=state, max_items=max_items)]


# --- the shop as a whole ---------------------------------------------------------
#
# audit_listing() judges one listing in isolation, which is the right unit for most
# things — but it makes a whole class of problem invisible. If every listing in a shop
# carries the same tags, each one scores perfectly while they all compete with each
# other for the same query, and Etsy generally surfaces only one or two listings from
# the same shop in a result. Nothing you can see from inside a single listing tells you
# that. So this looks across the shop instead.

# A tag on more than this share of a shop's listings is doing shop-level work, not
# listing-level work. It is not wasted — it is how the shop becomes eligible at all —
# but it no longer distinguishes one listing from its siblings.
SHOP_WIDE_SHARE = 0.5

# Below this many distinguishing tags, a listing has little to say that its siblings
# do not already say.
MIN_DISTINCTIVE_TAGS = 5


@dataclass
class ShopAudit:
    listings: int
    shop_wide: list[tuple[str, int]] = field(default_factory=list)
    """Tags carried by more than SHOP_WIDE_SHARE of the shop, most common first."""
    distinctive_per_listing: dict[int, int] = field(default_factory=dict)
    crowded: list[tuple[int, str, int]] = field(default_factory=list)
    """(listing_id, title, distinctive count) for listings with too little of their own."""
    issues: list[Issue] = field(default_factory=list)

    @property
    def median_distinctive(self) -> float:
        values = sorted(self.distinctive_per_listing.values())
        return statistics.median(values) if values else 0.0


def audit_shop(listings: Sequence[dict[str, Any]]) -> ShopAudit:
    """Find problems that only exist between listings, not inside them."""
    total = len(listings)
    result = ShopAudit(listings=total)
    if total < 2:
        return result

    counts: Counter[str] = Counter()
    per_listing: dict[int, set[str]] = {}
    for listing in listings:
        tags = {
            cleaned
            for tag in (listing.get("tags") or [])
            if (cleaned := str(tag).strip().lower())
        }
        per_listing[int(listing.get("listing_id") or 0)] = tags
        counts.update(tags)

    cutoff = max(2, int(total * SHOP_WIDE_SHARE))
    shop_wide = {tag for tag, n in counts.items() if n >= cutoff}
    result.shop_wide = sorted(
        ((tag, counts[tag]) for tag in shop_wide), key=lambda kv: -kv[1]
    )

    for listing in listings:
        listing_id = int(listing.get("listing_id") or 0)
        distinctive = len(per_listing[listing_id] - shop_wide)
        result.distinctive_per_listing[listing_id] = distinctive
        if distinctive < MIN_DISTINCTIVE_TAGS:
            result.crowded.append((listing_id, str(listing.get("title", ""))[:60], distinctive))

    result.crowded.sort(key=lambda row: row[2])

    if result.shop_wide:
        share = result.shop_wide[0][1] / total
        result.issues.append(
            Issue(
                "shop.tag_overlap",
                "warn" if share < 0.8 else "error",
                f"{len(result.shop_wide)} tag(s) appear on at least {cutoff} of your "
                f"{total} listings. They win the shop a place in those searches, but "
                f"they cannot separate one listing from another — and Etsy rarely shows "
                f"two listings from the same shop in one result.",
            )
        )
    if result.crowded:
        result.issues.append(
            Issue(
                "shop.too_alike",
                "warn",
                f"{len(result.crowded)} listing(s) have fewer than {MIN_DISTINCTIVE_TAGS} "
                f"tags their siblings do not already use. Those listings mostly compete "
                f"with each other rather than reaching new buyers.",
            )
        )
    return result


def overlapping_pairs(
    listings: Sequence[dict[str, Any]], *, limit: int = 5, threshold: float = 0.7
) -> list[tuple[str, str, float]]:
    """The listing pairs most likely to be cannibalising each other, worst first."""
    prepared = [
        (
            str(listing.get("title", ""))[:44],
            {str(t).strip().lower() for t in (listing.get("tags") or []) if str(t).strip()},
        )
        for listing in listings
    ]
    pairs = []
    for i, (title_a, tags_a) in enumerate(prepared):
        for title_b, tags_b in prepared[i + 1:]:
            union = tags_a | tags_b
            if not union:
                continue
            similarity = len(tags_a & tags_b) / len(union)
            if similarity >= threshold:
                pairs.append((title_a, title_b, similarity))
    pairs.sort(key=lambda row: -row[2])
    return pairs[:limit]


# --- market research ------------------------------------------------------------

# How much of the market a report keeps. Titles and tags are built from phrases, so most
# rows are phrases of two to four words. Single words are kept too, but fewer: they tell
# which product and which audience the market is about, and are too broad to rank for.
TAG_ROWS = 40
PHRASE_ROWS = 70
SINGLE_WORD_ROWS = 20
MAX_PHRASE_WORDS = 4

# A word of a title: letters and digits, joined inside by a hyphen or an apostrophe
# (T-Shirt, Mother's, Mid-Century) and, between two digits, by a point, a comma or a
# slash (8.5x11, 1,000, 3/4). "11oz" and "8x10" are one word too.
_TITLE_WORD = re.compile(r"[^\W_]+(?:(?:['-]|(?<=\d)[.,/](?=\d))[^\W_]+)*")
# A title is a chain of phrases: "Retro Sunset Shirt, Hiking Gift | Camping Tee - Unisex".
# Inside one phrase, words are separated by spaces and at most a mark that does not end
# it: an abbreviation's full stop (St. Patrick's Day), quotes, "#" (#1 Dad), ™ © ®.
# Anything else ends the phrase: a comma, a pipe, a dash, a slash, a bracket, "&", "+",
# a colon, an emoji.
_SOFT_GAP = re.compile(r"[\s.'\"“”#*™©®]*")
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'",
                              "´": "'"})


@dataclass
class MarketReport:
    keyword: str
    sampled: int
    tags: list[tuple[str, int]]
    """The ranking listings' tags and how many listings use each, most used first."""
    phrases: list[tuple[str, int]]
    """Their title phrases and how many listings use each, most used first: 1-4 words,
    never across a comma, a pipe or a dash, mostly multi-word, and without the shorter
    phrases that only ever appear inside a longer one."""
    price_min: float | None
    price_median: float | None
    price_max: float | None
    currency: str
    median_favorers: float | None
    top_listings: list[dict[str, Any]]
    # How many of the sampled listings the price band actually covers, and how many
    # distinct currencies turned up. Etsy prices each listing in its own shop's
    # currency, so the band describes one currency, not the whole sample.
    price_sample: int = 0
    currency_count: int = 0

    @property
    def empty(self) -> bool:
        return self.sampled == 0

    @property
    def price_band_is_partial(self) -> bool:
        return self.currency_count > 1


def _price(listing: dict[str, Any]) -> tuple[float | None, str]:
    price = listing.get("price") or {}
    amount, divisor = price.get("amount"), price.get("divisor") or 100
    if not isinstance(amount, (int, float)):
        return None, ""
    return amount / divisor, str(price.get("currency_code", ""))


def ngrams(tokens: Sequence[str], size: int) -> Iterable[str]:
    for i in range(len(tokens) - size + 1):
        window = tokens[i:i + size]
        if any(w in STOPWORDS for w in window):
            continue
        yield " ".join(window)


def _lower(word: str) -> str:
    # "İ".lower() is "i" plus a combining dot; a buyer types a plain "i".
    return word.replace("İ", "i").lower()


def title_segments(title: str) -> list[list[str]]:
    """A listing title split into its phrases, each a list of lower-case words.

    "Retro Sunset T-Shirt, Mother's Day Gift | 11oz Mug" gives
    [["retro", "sunset", "t-shirt"], ["mother's", "day", "gift"], ["11oz", "mug"]].
    """
    text = unicodedata.normalize("NFC", str(title or "")).translate(_APOSTROPHES)
    segments: list[list[str]] = []
    current: list[str] = []
    end = 0
    for match in _TITLE_WORD.finditer(text):
        if current and not _SOFT_GAP.fullmatch(text, end, match.start()):
            segments.append(current)
            current = []
        current.append(_lower(match.group()))
        end = match.end()
    if current:
        segments.append(current)
    return segments


def _useful_word(word: str) -> bool:
    """A single word worth a row: no stopword, at least 3 characters, not only digits."""
    return word not in STOPWORDS and len(word) >= 3 and any(ch.isalpha() for ch in word)


def title_phrases(title: str, *, longest: int = MAX_PHRASE_WORDS) -> set[str]:
    """Every phrase of 1 to `longest` words in a title, each once.

    A phrase stays inside one of the title's own phrases: "Hiking Shirt, Gift for Him"
    yields "hiking shirt", never "shirt gift". Like `ngrams`, it never spans a stopword.
    Nor does it say a word twice: "mug ceramic mug" in "Ceramic Mug Ceramic Coffee Cup"
    is where two phrases meet without a comma (a repeat such as "ho ho ho" is kept).
    """
    found: set[str] = set()
    for segment in title_segments(title):
        for size in range(1, longest + 1):
            for gram in ngrams(segment, size):
                if size == 1:
                    if _useful_word(gram):
                        found.add(gram)
                    continue
                distinct = len(set(gram.split(" ")))
                if distinct == size or distinct == 1:
                    found.add(gram)
    return found


def _by_count(row: tuple[str, int]) -> tuple[int, str]:
    """Most used first, equally used ones alphabetically: a report never depends on the
    order a set happened to be iterated in."""
    return -row[1], row[0]


def _fragments(counts: dict[str, int]) -> set[str]:
    """Multi-word phrases that never appear without one particular longer phrase.

    If every listing that says "graphic tee" says "vintage graphic tee", the shorter
    one is a piece of the longer, not a phrase of its own. Single words stay: they show
    which product and which audience the market is about.
    """
    pieces: set[str] = set()
    for phrase, count in counts.items():
        words = phrase.split(" ")
        for size in range(2, len(words)):
            for start in range(len(words) - size + 1):
                piece = " ".join(words[start:start + size])
                if counts.get(piece) == count:
                    pieces.add(piece)
    return pieces


def _top_phrases(counter: Counter[str], sampled: int) -> list[tuple[str, int]]:
    # A phrase that appears once is noise, not a pattern.
    floor = max(2, sampled // 25)
    counts = {phrase: n for phrase, n in counter.items() if n >= floor}
    for piece in _fragments(counts):
        del counts[piece]
    ranked = sorted(counts.items(), key=_by_count)
    singles = [row for row in ranked if " " not in row[0]][:SINGLE_WORD_ROWS]
    longer = [row for row in ranked if " " in row[0]][:PHRASE_ROWS - len(singles)]
    return sorted(singles + longer, key=_by_count)


def research(
    client: EtsyClient,
    keyword: str,
    *,
    sample: int = 200,
    sort_on: str = "score",
    **filters: Any,
) -> MarketReport:
    """Sample the listings Etsy actually returns for a keyword and describe them.

    Tags and phrases are counted per listing and ordered by count, then alphabetically,
    so the same sample always gives the same report.
    """
    listings = list(
        client.search_active_listings(
            keywords=keyword, max_items=sample, sort_on=sort_on, sort_order="desc", **filters
        )
    )

    tag_counter: Counter[str] = Counter()
    phrase_counter: Counter[str] = Counter()
    prices_by_currency: dict[str, list[float]] = {}
    favorers: list[int] = []

    for listing in listings:
        # Count DISTINCT LISTINGS, not occurrences. These numbers are shown as
        # "appears in 41 of 200 listings", so a single title reading
        # "ceramic mug ceramic mug" must contribute 1 to `ceramic mug`, not 2 —
        # otherwise the share can exceed 100% and the label is simply untrue.
        seen_tags = {
            cleaned
            for tag in (listing.get("tags") or [])
            if (cleaned := _lower(" ".join(str(tag).split())))
        }
        tag_counter.update(seen_tags)
        phrase_counter.update(title_phrases(listing.get("title") or ""))

        price, code = _price(listing)
        if price is not None:
            prices_by_currency.setdefault(code or "?", []).append(price)

        fav = listing.get("num_favorers")
        if isinstance(fav, int):
            favorers.append(fav)

    # A marketplace-wide search returns each listing priced in its own shop's
    # currency. Pooling them would make min/median/max arithmetic over
    # incommensurable numbers, so report the band for the single most common
    # currency and carry the coverage so the caller can say so out loud.
    currency, prices = "", []
    if prices_by_currency:
        currency, prices = max(prices_by_currency.items(), key=lambda kv: len(kv[1]))

    top = sorted(listings, key=lambda x: x.get("num_favorers") or 0, reverse=True)[:10]
    top_rows = []
    for listing in top:
        price, code = _price(listing)
        top_rows.append(
            {
                "listing_id": listing.get("listing_id"),
                "title": listing.get("title", ""),
                "price": f"{price:.2f}" if price is not None else "",
                "currency": code,
                "num_favorers": listing.get("num_favorers", 0),
                "tags": listing.get("tags") or [],
                "url": listing.get("url", ""),
            }
        )

    return MarketReport(
        keyword=keyword,
        sampled=len(listings),
        tags=sorted(tag_counter.items(), key=_by_count)[:TAG_ROWS],
        phrases=_top_phrases(phrase_counter, len(listings)),
        price_min=min(prices) if prices else None,
        price_median=statistics.median(prices) if prices else None,
        price_max=max(prices) if prices else None,
        currency=currency,
        median_favorers=statistics.median(favorers) if favorers else None,
        top_listings=top_rows,
        price_sample=len(prices),
        currency_count=len(prices_by_currency),
    )


@dataclass
class TagSuggestions:
    add_now: list[str]
    """Fits in the free slots — can be added without removing anything."""

    needs_a_swap: list[str]
    """Also common in this market, but only fits if an existing tag is dropped."""

    free_slots: int
    used_slots: int

    def __iter__(self):
        return iter(self.add_now)

    def __len__(self) -> int:
        return len(self.add_now)


def suggest_tags(
    report: MarketReport, *, existing: Sequence[str] = (), extra: int = 5
) -> TagSuggestions:
    """Tags common in the ranking set that this listing does not use yet.

    Split by whether they actually fit. Offering 13 suggestions to a listing with 12
    tags implies you can add 13 more; Etsy's ceiling is 13 in total, so only one
    would land. The rest are a genuine option, but only as a swap — say which.
    """
    have = {t.lower().strip() for t in existing if str(t).strip()}
    free = max(0, MAX_TAGS - len(have))

    candidates = [
        tag for tag, _count in report.tags if tag not in have and len(tag) <= MAX_TAG_LEN
    ]
    return TagSuggestions(
        add_now=candidates[:free],
        needs_a_swap=candidates[free : free + extra],
        free_slots=free,
        used_slots=len(have),
    )
