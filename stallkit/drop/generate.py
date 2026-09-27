"""Building a title and thirteen tags from a concept and what actually ranks.

There is no search volume to work from — Etsy publishes none — so this does the only
honest thing available: it looks at the listings Etsy returns for the concept and
reuses the vocabulary they share. That is a measurement, not a prediction, and the
interface says so wherever these numbers appear.

A title is built the way a good Etsy title reads: the concept and the product first
("Retro Mountain Sunset Shirt"), then three or four market phrases that each add a new
search ("Nature Lover Gift, Hiking Shirt, Outdoor Adventure Tee…"), and who it is for
last when the market says so. Single words on their own ("Gift", "Him") never make a
segment, a phrase that only repeats the title is skipped, and a phrase about another
product ("Vinyl Sticker" in a shirt's title, because the concept search also returned
stickers) is left out. Claims about the physical product — a size, a material, a
blank's brand, "personalized" — are only used when the seller's own template listing
makes them too: the market's shirts may be Comfort Colors, the seller's may not be.

Everything here is deliberately deterministic. The same design and the same market
sample produce the same title twice, which is what makes a hundred-product batch
reviewable: a seller who checks ten rows has learned something about the other ninety.

The hard limits are enforced at the point of construction, not checked afterwards, so
this module cannot emit something `listings.build_payload` would reject: 140
characters, Etsy's title characters (letters, digits, punctuation, maths symbols and
spaces; "%", ":", "&" and "+" once each), thirteen tags of at most twenty characters.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Union

from ..config import MAX_TAG_LEN, MAX_TAGS, MAX_TITLE_LEN
from ..listings import TITLE_ONCE, bad_tag_chars, title_char_ok
from ..seo import STOPWORDS
from ..seo import words as _seo_words
from .seeds import Seed, fold

if TYPE_CHECKING:  # pragma: no cover
    from ..seo import MarketReport

# The seller's own text about the product: the template's title, tags, description.
Hint = Union[str, Sequence[str], None]

# The concept and product, then at most four market phrases. Etsy truncates a title on a
# mobile card after about 40 characters, so the concept must land inside that window;
# everything after is for the search index, not the buyer's eye.
MAX_TITLE_SEGMENTS = 5
# At most this many product nouns in one title ("Shirt … T-Shirt … Tee … Shirt"), and
# the same one at most twice.
MAX_TITLE_NOUNS = 4
# At most this many phrases may bring a word of the concept back ("Dog Dad Shirt, Dog
# Lover Gift"): once or twice reads naturally, on every segment it is stuffing.
MAX_ECHOES = 2

# --- vocabulary ---------------------------------------------------------------------

# Product nouns, as tokens -> (family, how a title spells it). The family keeps a mug's
# title from borrowing "Poster" from a concept search that also returned posters.
_PRODUCTS: dict[tuple[str, ...], tuple[str, str]] = {
    ("tshirt",): ("shirt", "T-Shirt"),
    ("shirt",): ("shirt", "Shirt"),
    ("tee",): ("shirt", "Tee"),
    ("tank", "top"): ("tank", "Tank Top"),
    ("tank",): ("tank", "Tank"),
    ("sweatshirt",): ("sweatshirt", "Sweatshirt"),
    ("crewneck",): ("sweatshirt", "Crewneck"),
    ("sweater",): ("sweatshirt", "Sweater"),
    ("pullover",): ("sweatshirt", "Pullover"),
    ("hoodie",): ("hoodie", "Hoodie"),
    ("onesie",): ("baby", "Onesie"),
    ("bodysuit",): ("baby", "Bodysuit"),
    ("mug",): ("mug", "Mug"),
    ("cup",): ("mug", "Cup"),
    ("tumbler",): ("tumbler", "Tumbler"),
    ("water", "bottle"): ("bottle", "Water Bottle"),
    ("poster",): ("wallart", "Poster"),
    ("print",): ("wallart", "Print"),
    ("wall", "art"): ("wallart", "Wall Art"),
    ("art", "print"): ("wallart", "Art Print"),
    ("wall", "decor"): ("wallart", "Wall Decor"),
    ("canvas", "print"): ("wallart", "Canvas Print"),
    ("art",): ("wallart", "Art"),
    ("home", "decor"): ("decor", "Home Decor"),
    ("decor",): ("decor", "Decor"),
    ("sticker",): ("sticker", "Sticker"),
    ("decal",): ("sticker", "Decal"),
    ("tote", "bag"): ("tote", "Tote Bag"),
    ("tote",): ("tote", "Tote"),
    ("bag",): ("tote", "Bag"),
    ("phone", "case"): ("phonecase", "Phone Case"),
    ("iphone", "case"): ("phonecase", "iPhone Case"),
    ("pillow", "case"): ("pillow", "Pillow Case"),
    ("pencil", "case"): ("pencilcase", "Pencil Case"),
    ("case",): ("phonecase", "Case"),
    ("pillow",): ("pillow", "Pillow"),
    ("cushion",): ("pillow", "Cushion"),
    ("blanket",): ("blanket", "Blanket"),
    ("ornament",): ("ornament", "Ornament"),
    ("magnet",): ("magnet", "Magnet"),
    ("keychain",): ("keychain", "Keychain"),
    ("hat",): ("hat", "Hat"),
    ("cap",): ("hat", "Cap"),
    ("beanie",): ("hat", "Beanie"),
    ("sock",): ("socks", "Socks"),
    ("apron",): ("apron", "Apron"),
    ("coaster",): ("coaster", "Coaster"),
    ("notebook",): ("notebook", "Notebook"),
    ("journal",): ("notebook", "Journal"),
    ("card",): ("card", "Card"),
    ("bookmark",): ("bookmark", "Bookmark"),
    ("puzzle",): ("puzzle", "Puzzle"),
    ("towel",): ("towel", "Towel"),
    ("flag",): ("flag", "Flag"),
    ("garden", "stake"): ("stake", "Garden Stake"),
    ("necklace",): ("jewelry", "Necklace"),
    ("earring",): ("jewelry", "Earrings"),
    ("bracelet",): ("jewelry", "Bracelet"),
    ("svg",): ("digital", "SVG"),
    ("png",): ("digital", "PNG"),
    ("clipart",): ("digital", "Clipart"),
    ("digital", "download"): ("digital", "Digital Download"),
}
_NOUN_WORDS = {word for key in _PRODUCTS for word in key}
# Phrases about one kind that suit another: "Living Room Decor" on a print or a pillow.
_FITS = {"decor": {"wallart", "pillow", "blanket", "flag", "stake", "ornament", "coaster",
                   "towel", "puzzle"}}
# Too vague to name the product at the head of a title on their own.
_NOT_A_HEAD = {"Art", "Decor", "Home Decor", "Case", "Bag", "Cup", "Print"}
# Garments, where "for Men and Women" is how Etsy titles name the audience.
_APPAREL = {"shirt", "tank", "sweatshirt", "hoodie", "baby", "hat", "socks"}
# A physical product's title must not promise a file.
_DIGITAL_WORDS = {"printable", "digital", "download", "downloadable", "instant", "svg",
                  "png", "pdf", "jpg", "sublimation"}

# Words that carry no search of their own: a phrase must add something besides these.
_GENERIC = {
    "gift", "gifts", "present", "presents", "idea", "ideas", "best", "perfect", "unique",
    "new", "sale", "awesome", "great", "clothing", "apparel", "clothes", "outfit", "item",
    "items", "design", "designs", "top", "quality", "premium", "shop", "store", "style",
    "stuff",
}
# Real searches, but marketplace-wide ones: a phrase whose only new words are these
# ("Graphic Tee", "Birthday Gift") ranks below one about the design's own theme.
_WEAK = {
    "graphic", "cute", "funny", "trendy", "aesthetic", "birthday", "vintage", "retro",
    "cool", "modern", "classic", "simple", "basic", "stocking", "stuffer", "christmas",
    "holiday", "summer", "everyday", "casual", "soft", "comfy",
}
_AUDIENCE_WOMEN = {"her", "women", "womens", "woman", "ladies", "lady"}
_AUDIENCE_MEN = {"him", "men", "mens", "man", "guys", "guy"}
_AUDIENCE = _AUDIENCE_WOMEN | _AUDIENCE_MEN | {
    "unisex", "kids", "kid", "boys", "girls", "boy", "girl", "toddler", "toddlers", "youth",
    "adult", "adults", "teens", "teen",
}
# What the product is made of or how it is made: true of some listings in the market,
# not necessarily of this one. Used only when the seller's own template says so, and so
# is a size (11oz, 8x10, 20oz); "Class of 2026" or "40th Birthday" is the design's theme.
_CLAIMS = {
    "personalized", "personalised", "custom", "customized", "customised", "monogram",
    "monogrammed", "engraved", "embroidered", "handmade", "handpainted", "hand", "painted",
    "comfort", "colors", "colours", "color", "colour", "gildan", "bella", "canvas", "cotton",
    "organic", "ceramic", "enamel", "glass", "stainless", "steel", "vinyl", "waterproof",
    "glossy", "matte", "holographic", "die", "kiss", "framed", "unframed", "oversized",
    "slim", "protective", "magsafe", "set", "bundle", "pack", "piece", "pieces", "wood",
    "wooden", "metal", "leather", "linen", "silk", "gold", "silver", "sterling", "plated",
    "iphone", "samsung", "galaxy", "pixel", "matching", "name", "names", "photo", "free",
    "shipping", "discount", "bestseller", "reusable", "insulated", "dishwasher",
    "microwave", "safe", "large", "small", "mini", "xl",
}
# A word that cannot start a phrase: "Lover Gift" is half of "Nature Lover Gift".
_DEPENDENT_START = {"lover", "lovers", "loving", "themed", "inspired", "style", "shaped"}
# After a product noun or "gift", only these may follow inside one phrase; anything else
# means the n-gram ran across a comma in the listing's title ("shirt hiking shirt").
_AFTER_HEAD = {"gift", "gifts", "set", "idea", "ideas", "box", "card", "bag", "basket",
               "tag", "wrap", "guide", "bundle"}
_HEADS = {"gift", "gifts"}
_MINOR = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the",
          "to", "with", "vs"}
_CASING = {
    "tshirt": "T-Shirt", "t-shirt": "T-Shirt", "iphone": "iPhone", "ipad": "iPad",
    "airpods": "AirPods", "diy": "DIY", "svg": "SVG", "png": "PNG", "pdf": "PDF",
    "usa": "USA", "uk": "UK", "lgbt": "LGBT", "lgbtq": "LGBTQ", "bff": "BFF", "3d": "3D",
    "xl": "XL", "xxl": "XXL", "nyc": "NYC",
}
# Words that never make a title about one design: joining words, generic and weak ones,
# who it is for, "lover", "gift", claims about the product (material, size, brand).
_PLAIN = (_GENERIC | _WEAK | _AUDIENCE | _DEPENDENT_START | _HEADS | _MINOR | _CLAIMS
          | _DIGITAL_WORDS | {"graphic", "top", "tee", "gift", "unisex"})
# How a product noun is spelled in a tag: Etsy tags take letters, digits, spaces, - and '.
_TAG_NOUNS = {"T-Shirt": "tshirt", "iPhone Case": "iphone case"}
# Product names that are a search of their own, not a synonym: "iphone case" is not
# "phone case", while "t-shirt", "tee" and "shirt" are one search to a tag list.
_OWN_SEARCH = {"iPhone Case"}
# A number glued to its unit, the way sellers write it: 11oz, 8x10, 70s, 3d.
_UNITS = {"oz", "ml", "l", "cm", "mm", "inch", "in", "ft", "s", "th", "st", "nd", "rd", "d",
          "x", "k", "pcs", "pc"}
_MEASURE = re.compile(r"^\d+(oz|ml|l|cl|cm|mm|in|inch|inches|ft|pcs|pc|pk|x\d+[a-z]*)$")
# Letters only Turkish (among the languages sellers here write in) spells with: a text
# holding one is Turkish, and its "i" capitalises to "İ" ("Kedi Pati İzi").
_TURKISH_LETTERS = set("çğıöşüÇĞİÖŞÜ")


@dataclass
class Generated:
    title: str
    tags: list[str]
    description: str = ""
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def is_turkish(*texts: object) -> bool:
    """Whether any of these texts is written in Turkish (holds a Turkish-only letter)."""
    return any(_TURKISH_LETTERS & set(str(text or "")) for text in texts)


def _upper_first(part: str, turkish: bool) -> str:
    first = part[:1]
    if turkish and first == "i":
        return "İ" + part[1:]
    return first.upper() + part[1:]


def titlecase(text: str, *, turkish: bool | None = None) -> str:
    """Capitalise each word the way a listing title does.

    Without str.title()'s habit of mangling apostrophes ("Mom'S"); small joining words
    stay small after the first ("Shirt for Men and Women"); a few words keep their own
    spelling ("T-Shirt", "iPhone", "DIY"); "11oz" and "70s" stay as they are. Turkish
    text capitalises "i" to "İ" ("kedi pati izi" -> "Kedi Pati İzi"); `turkish=None`
    decides from the text itself (is_turkish), so English "ice" stays "Ice".
    """
    if turkish is None:
        turkish = is_turkish(text)
    out = []
    for index, word in enumerate(text.split()):
        low = word.lower()
        if low in _CASING:
            out.append(_CASING[low])
        elif index and low in _MINOR:
            out.append(low)
        elif word[:1].isdigit():
            out.append(word)
        else:
            out.append("-".join(_upper_first(part, turkish) for part in word.split("-")))
    return " ".join(out)


def clean_tag(raw: str) -> str:
    """Strip a tag down to something Etsy will accept, or return '' if nothing is left."""
    text = " ".join(str(raw or "").split()).strip().lower()
    if not text:
        return ""
    bad = bad_tag_chars(text)
    if bad:
        text = " ".join("".join(ch for ch in text if ch not in bad).split())
    return text if len(text) <= MAX_TAG_LEN else ""


def hint_from(title: str = "", tags: Sequence[str] | None = None,
              description: str = "") -> list[str]:
    """The seller's own words about the product, in the order the builders trust them.

    `hint_from(template.source_title, template.tags, template.description)` is what
    `build_title(..., product_hint=)` and `build_tags(..., product_hint=)` expect.
    """
    return [str(text) for text in (title, *(tags or []), description) if text]


def research_keyword(seed: Seed, hint: Hint = None) -> str:
    """What to search Etsy for: the concept, plus the product when it does not say it.

    The template listing decides what the product is (as for the title): "dog dad paw
    print" on a shirt template searches "dog dad paw print shirt", where the concept
    alone returns posters ("print") and leaves the shirt's title nothing to borrow. A
    concept that already names the product ("retro sunset mug"), or ends in "gift", is
    searched as it is, and so is any concept when the template names no product.
    """
    text = " ".join(seed.text.split())
    product = _product(seed, None, hint, [])
    tokens = _tokens(text)
    if not product.family or product.in_concept or not tokens or tokens[-1] in _HEADS:
        return text
    return f"{text} {product.display.lower()}"


def _distinctive(text: str) -> set[str]:
    """The words that make a title about one design: not the product, not generic."""
    tokens = _tokens(text)
    nouns = {i for start, end, *_ in _nouns(tokens) for i in range(start, end)}
    return {
        _key(token) for index, token in enumerate(tokens)
        if index not in nouns and token not in STOPWORDS and token not in _PLAIN
        and _key(token) not in _PLAIN and not token[:1].isdigit() and len(_key(token)) > 1
    }


def product_tags(tags: Sequence[str] | None, template_title: str = "",
                 seed: Seed | None = None) -> list[str]:
    """The template listing's tags that suit any design of its product, cleaned.

    Its tags about its own design are left out: a tag holding a distinctive word of the
    template's title ("retro mountain sun", "hiking gift" on a "Retro Mountain Sunset
    Shirt, ... Hiking Gift") would put another design's words on this one. A word the
    new design shares ("mountain" for "mountain goat trail") is fine. Without a title
    nothing is known to be the template's own, so every tag is kept.
    """
    own = _distinctive(template_title) - (_distinctive(seed.text) if seed else set())

    def about_the_template(tag: str) -> bool:
        # A word of it, or the start of one: Etsy's 20 characters cut "retro mountain
        # sunset" to "retro mountain sun".
        return any(
            word in own or (len(word) >= 3 and any(d.startswith(word) for d in own))
            for word in _distinctive(tag)
        )

    out: list[str] = []
    for raw in tags or []:
        tag = clean_tag(raw)
        if tag and tag not in out and not (own and about_the_template(tag)):
            out.append(tag)
    return out


# --- words and phrases --------------------------------------------------------------


def _singular(token: str) -> str:
    if token.endswith("s") and token[:-1] in _NOUN_WORDS:
        return token[:-1]
    return token


def _key(token: str) -> str:
    """What makes two spellings the same word: case, accents, a plural "s"."""
    key = _singular(fold(token).replace("'", ""))
    if len(key) > 3 and key.endswith("s") and not key.endswith(("ss", "us", "is")):
        key = key[:-1]
    return key


def _tokens(text: str) -> list[str]:
    """Words as seo.research counts them, with what it splits put back together.

    "T-Shirt" is one word ("tshirt"), and so are "11oz", "8x10" and "70s", which the
    word pattern cuts into a number and a unit.
    """
    raw = _seo_words(unicodedata.normalize("NFC", str(text or "")))
    out: list[str] = []
    for word in raw:
        if word in ("shirt", "shirts") and out and out[-1] in ("t", "tee"):
            out[-1] = "tshirt"
        elif out and out[-1][:1].isdigit() and (
            (word in _UNITS and not out[-1][-1:].isalpha())
            or (word.isdigit() and out[-1].endswith("x"))
        ):
            out[-1] += word
        else:
            out.append(word)
    return out


def _nouns(tokens: Sequence[str]) -> list[tuple[int, int, str, str]]:
    """(start, end, family, display) for each product noun, longest match first."""
    found = []
    index = 0
    singular = [_singular(t) for t in tokens]
    while index < len(tokens):
        pair = tuple(singular[index:index + 2])
        if len(pair) == 2 and pair in _PRODUCTS:
            found.append((index, index + 2, *_PRODUCTS[pair]))
            index += 2
            continue
        one = (singular[index],)
        if one in _PRODUCTS:
            found.append((index, index + 1, *_PRODUCTS[one]))
        index += 1
    return found


def _is_claim(token: str) -> bool:
    folded = fold(token)
    return folded in _CLAIMS or _key(token) in _CLAIMS or bool(_MEASURE.match(folded))


@dataclass
class _Phrase:
    tokens: tuple[str, ...]
    count: int
    tagged: bool = False  # a seller chose it as a tag, rather than it being a title n-gram
    fragment: bool = False  # a piece of a longer phrase the market uses as often

    def __post_init__(self) -> None:
        self.keys = [_key(t) for t in self.tokens]
        self.nouns = _nouns(self.tokens)
        self.noun_positions = {i for start, end, *_ in self.nouns for i in range(start, end)}
        self.family = self.nouns[-1][2] if self.nouns else None
        # The head noun is the product; another product's noun before it describes it
        # ("Water Bottle Decal", "Coffee Mug Sticker") and counts as a word.
        head = set(range(self.nouns[-1][0], self.nouns[-1][1])) if self.nouns else set()
        self.words = {k for i, k in enumerate(self.keys)
                      if i not in head and k not in STOPWORDS}
        self.text = " ".join(self.tokens)
        self.claims = {t for t in self.tokens if _is_claim(t)}

    @property
    def broken(self) -> bool:
        """An n-gram that ran across a comma, or half of a phrase."""
        tokens = self.tokens
        if len(tokens) < 2 or tokens[0] in STOPWORDS or tokens[-1] in STOPWORDS:
            return True
        if tokens[0] in _DEPENDENT_START:
            return True
        if any(len(t) == 1 and t.isalpha() for t in tokens):
            return True
        heads = {end - 1 for _start, end, *_ in self.nouns}
        heads |= {i for i, t in enumerate(tokens) if t in _HEADS}
        for i in heads:
            if i + 1 < len(tokens):
                follower = tokens[i + 1]
                if not (follower in STOPWORDS or follower in _AFTER_HEAD
                        or (i + 1) in self.noun_positions):
                    return True
        return False

    @property
    def audience(self) -> set[str]:
        return {k for k in self.keys if k in _AUDIENCE}

    def meaningful(self) -> set[str]:
        return {k for k in self.words
                if k not in _GENERIC and k not in _AUDIENCE and len(k) > 1}

    def novelty(self, used: dict[str, int]) -> tuple[float, set[str]]:
        """How much new search this phrase adds: a theme word counts 1, "gift" or
        "lover" a half, so "Coffee Lover Gift" still adds to "But First Coffee Mug"."""
        new = {k for k in self.meaningful() if k not in used}
        if new:
            return float(len(new)), new
        generic = {k for k in self.words if k in _GENERIC and k not in used}
        return 0.5 * len(generic), new


# What an HTML entity leaves once the word pattern has cut it up: "mother&#39;s" was
# counted as "mother 39 s". A research cache written before titles were decoded (up to
# seven days old) can still hold these.
_ENTITY_BITS = {"39", "34", "amp", "quot", "apos", "nbsp", "x27"}
# A number joined to the next by a point, a comma or a slash: "8.5x11", "3/4", "1,000",
# "12.5oz". seo.research keeps such a size whole, but _tokens splits it again ("8 5x11",
# "3 4 sleeve"), and Etsy tags cannot hold ".", "," or "/" at all; the row is left out.
_JOINED_NUMBER = re.compile(r"\d[.,/]\d")


def _rows(rows: object) -> list[tuple[str, int]]:
    out = []
    for row in rows or []:  # type: ignore[union-attr]
        try:
            text, count = html.unescape(str(row[0])), int(row[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if _ENTITY_BITS & set(_seo_words(text)) or _JOINED_NUMBER.search(text):
            continue
        out.append((text, count))
    return out


def _has_market(report: MarketReport | None) -> bool:
    return report is not None and not getattr(report, "empty", True)


def _market_phrases(report: MarketReport | None) -> list[_Phrase]:
    """Every multi-word phrase the market uses, from its titles and its tags, merged."""
    if not _has_market(report):
        return []
    assert report is not None
    merged: dict[tuple[str, ...], _Phrase] = {}
    for rows, tagged in ((report.tags, True), (report.phrases, False)):
        for text, count in _rows(rows):
            tokens = tuple(_tokens(text))
            if len(tokens) < 2:
                continue
            known = merged.get(tokens)
            if known is None:
                merged[tokens] = _Phrase(tokens, count, tagged)
            else:
                known.count = max(known.count, count)
                known.tagged = known.tagged or tagged
    phrases = list(merged.values())
    # "Player Tee" out of "Pickleball Player Tee", "Year Gift" out of "End of Year Gift":
    # an n-gram that only ever appears inside a longer phrase is not a phrase of its own.
    # (Title n-grams never span "of" or "for", so one cut there is always a piece.)
    whole = [p for p in phrases if not p.broken]
    for short in phrases:
        if short.tagged:
            continue
        size = len(short.tokens)
        for longer in whole:
            if len(longer.tokens) <= size:
                continue
            starts = [i for i in range(len(longer.tokens) - size + 1)
                      if longer.tokens[i:i + size] == short.tokens]
            cut_at_stopword = any(i and longer.tokens[i - 1] in STOPWORDS for i in starts)
            if starts and (cut_at_stopword or longer.count >= 0.8 * short.count):
                short.fragment = True
                break
    return phrases


def _word_counts(report: MarketReport | None) -> dict[str, int]:
    """How many sampled listings use each single word, from titles and tags."""
    counts: dict[str, int] = {}
    if not _has_market(report):
        return counts
    assert report is not None
    for rows in (report.phrases, report.tags):
        for text, count in _rows(rows):
            tokens = _tokens(text)
            if len(tokens) == 1:
                key = _singular(tokens[0])
                counts[key] = max(counts.get(key, 0), count)
    return counts


def _share(count: int, report: MarketReport | None) -> float:
    sampled = int(getattr(report, "sampled", 0) or 0) if report is not None else 0
    return count / sampled if sampled else 0.0


# --- what the product is ------------------------------------------------------------


@dataclass
class _Product:
    family: str | None = None
    display: str = ""
    in_concept: bool = False
    audience: set[str] = field(default_factory=set)  # women / men, as the template says
    claims: set[str] = field(default_factory=set)  # claim words the seller's text makes


def _hint_texts(hint: Hint) -> list[str]:
    if not hint:
        return []
    if isinstance(hint, str):
        return [hint]
    return [str(h) for h in hint if h]


def _product(seed: Seed, report: MarketReport | None, hint: Hint,
             phrases: list[_Phrase]) -> _Product:
    """Which product this listing is: named in the file, in the template, or by the market."""
    texts = _hint_texts(hint)
    hint_tokens = [t for text in texts for t in _tokens(text)]
    product = _Product(claims={fold(t) for t in hint_tokens if _is_claim(t)})
    product.claims |= {_key(t) for t in hint_tokens if _is_claim(t)}
    hint_keys = {_key(t) for t in hint_tokens}
    if hint_keys & _AUDIENCE_WOMEN:
        product.audience.add("women")
    if hint_keys & _AUDIENCE_MEN:
        product.audience.add("men")

    # The seller's template listing decides first: the draft is created in its category,
    # so a design called "route-66-poster" on a shirt template is a shirt. Its title's
    # opening names the product; else the kind its tags name most.
    tally: dict[str, int] = {}
    shown: dict[str, str] = {}
    for index, text in enumerate(texts):
        first = re.split(r"[,|/:;]| - ", text, maxsplit=1)[0]
        nouns = [n for n in _nouns(_tokens(first)) if n[2] not in _FITS]
        if index == 0 and nouns:
            tally = {nouns[-1][2]: 1}
            shown = {nouns[-1][2]: nouns[-1][3]}
            break
        for _s, _e, family, display in _nouns(_tokens(text)):
            tally[family] = tally.get(family, 0) + 1
            shown.setdefault(family, display)
    families = [f for f in sorted(tally) if f not in _FITS]
    concept = _nouns(_tokens(seed.text))
    if families:
        family = max(families, key=lambda f: tally[f])
        product.family, product.display = family, shown[family]
        # "Retro Sunset Mug" on a mug template: the name already says it.
        product.in_concept = any(f == family for _s, _e, f, _d in concept)
        return product

    # Then the file name, when it ends on what the product is: "retro-sunset-mug".
    tokens = _tokens(seed.text)
    for _start, end, family, display in reversed(concept):
        if family not in _FITS and all(t in _HEADS for t in tokens[end:]):
            product.family, product.display, product.in_concept = family, display, True
            return product

    # Otherwise the product most of the ranking listings are, when enough of them agree.
    if not _has_market(report):
        return product
    # How often each noun appears: on its own ("shirt") and inside phrases ("phone case").
    seen: dict[str, int] = {}
    kinds: dict[str, str] = {}
    for word, count in _word_counts(report).items():
        entry = _PRODUCTS.get((word,))
        if entry:
            seen[entry[1]] = max(seen.get(entry[1], 0), count)
            kinds[entry[1]] = entry[0]
    for phrase in phrases:
        if phrase.family and not phrase.broken:
            display = phrase.nouns[-1][3]
            seen[display] = max(seen.get(display, 0), phrase.count)
            kinds[display] = phrase.family
    totals: dict[str, int] = {}
    for display, count in seen.items():
        totals[kinds[display]] = max(totals.get(kinds[display], 0), count)
    families = [f for f in sorted(totals) if f not in _FITS]
    if not families:
        return product
    family = max(families, key=lambda f: totals[f])
    if _share(totals[family], report) < 0.2:
        return product
    # "Phone Case" rather than "Case", "Tote Bag" rather than "Bag", when the market
    # spells it out often enough.
    names = sorted((d for d in seen if kinds[d] == family), key=lambda d: (-seen[d], d))
    common = [d for d in names if d not in _NOT_A_HEAD and seen[d] >= 0.5 * seen[names[0]]]
    if common:
        longest = max(len(d.split()) for d in common)
        display = next(d for d in common if len(d.split()) == longest)
    else:
        display = names[0]
    product.family, product.display = family, display
    product.in_concept = any(f == family for _s, _e, f, _d in _nouns(_tokens(seed.text)))
    return product


# --- the title ----------------------------------------------------------------------


def _phrase_display(tokens: Sequence[str]) -> str:
    nouns = {start: (end, shown) for start, end, _f, shown in _nouns(tokens)}
    words: list[str] = []
    index = 0
    while index < len(tokens):
        if index in nouns:
            end, shown = nouns[index]
            words.append(shown)
            index = end
            continue
        words.append(tokens[index])
        index += 1
    return titlecase(" ".join(words))


def _title_safe(text: str) -> str:
    """Only what Etsy accepts in a title (listings.title_char_ok, TITLE_ONCE)."""
    text = unicodedata.normalize("NFC", text)
    kept = []
    for ch in text:
        if title_char_ok(ch):
            kept.append(ch)
        elif ch.isspace():
            kept.append(" ")
    text = "".join(kept)
    for ch, word in TITLE_ONCE.items():
        first = text.find(ch)
        if first >= 0 and text.count(ch) > 1:
            text = text[: first + 1] + text[first + 1:].replace(ch, f" {word} ")
    return " ".join(text.split())


def _fit(text: str, limit: int) -> str:
    """Cut at a word boundary, never through a word."""
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit + 1)
    return (text[:cut] if cut > 0 else text[:limit]).rstrip(" ,-")


def _allowed(phrase: _Phrase, product: _Product) -> bool:
    """A phrase about this product, that promises nothing the seller's own text does not."""
    if phrase.broken or phrase.fragment:
        return False
    if phrase.family is not None and not _fits(product.family, [phrase.family]):
        return False
    if product.family != "digital" and set(phrase.keys) & _DIGITAL_WORDS:
        return False
    return all(fold(c) in product.claims or _key(c) in product.claims for c in phrase.claims)


def _other_products(phrases: list[_Phrase], candidates: list[_Phrase],
                    product: _Product) -> list[_Phrase]:
    """Phrases that name no product but only ever go with another one.

    A concept search returns other products too; "Coffee Lover Gift" comes from the
    mugs among them, and the market shows it: "coffee" only appears in "coffee mug"
    and "coffee cup". On a sticker it would be a stranger's phrase.
    """
    families: dict[str, dict[str, int]] = {}
    for phrase in phrases:
        if phrase.family and not phrase.broken:
            for word in phrase.words:
                seen = families.setdefault(word, {})
                seen[phrase.family] = max(seen.get(phrase.family, 0), phrase.count)
    out = []
    for phrase in candidates:
        if phrase.family is not None:
            continue
        for word in phrase.meaningful():
            seen = families.get(word, {})
            if seen and not _fits(product.family, seen) and (
                phrase.count <= 1.2 * max(seen.values())
            ):
                out.append(phrase)
                break
    return out


def _spans(phrase: _Phrase, concept_last: str) -> bool:
    """A title n-gram that ran on past the concept: "sunset, gift…" read as one phrase.

    The concept opens most of the titles in its own search, so its last word followed
    by anything but this product's noun is the comma after it, not a phrase.
    """
    if phrase.tagged or not concept_last:
        return False
    nouns = {start for start, _end, *_ in phrase.nouns}
    return any(
        key == concept_last and index + 1 < len(phrase.keys) and index + 1 not in nouns
        for index, key in enumerate(phrase.keys)
    )


def _family_count(phrases: list[_Phrase], report: MarketReport | None,
                  family: str | None) -> int:
    """How many sampled listings name this kind of product, by its commonest noun."""
    best = 0
    for word, count in _word_counts(report).items():
        entry = _PRODUCTS.get((word,))
        if entry and entry[0] == family:
            best = max(best, count)
    for phrase in phrases:
        if phrase.family == family and not phrase.broken:
            best = max(best, phrase.count)
    return best


def _fits(family: str | None, families: object) -> bool:
    """Whether phrases about these product kinds may go on this one."""
    return any(f == family or family in _FITS.get(f, ()) for f in families)  # type: ignore


@dataclass
class _Plan:
    head: str
    segments: list[str]  # the chosen market phrases, as the title spells them
    chosen: list[_Phrase]
    phrases: list[_Phrase]  # every market phrase this product may use
    product: _Product
    concept_keys: set[str]


def _noun_word(display: str) -> str:
    """The word a reader counts: "Tote Bag" and "Bag" are both a bag."""
    return display.split()[-1].lower()


def _plan(seed: Seed, report: MarketReport | None = None, hint: Hint = None) -> _Plan:
    phrases = _market_phrases(report)
    product = _product(seed, report, hint, phrases)
    concept_tokens = _tokens(seed.text) or seed.text.split()
    concept_keys = {_key(t) for t in concept_tokens if t not in STOPWORDS}
    concept_last = _key(concept_tokens[-1]) if concept_tokens else ""

    # The seller's own name for the design, cased in its own language: Turkish when it,
    # or the template listing's text, is (a Turkish seller's "kedi pati izi").
    turkish = is_turkish(seed.text, *_hint_texts(hint))
    head = _title_safe(titlecase(seed.text, turkish=turkish))
    ends_in_gift = bool(concept_tokens) and concept_tokens[-1] in _HEADS
    if product.family and not product.in_concept and not ends_in_gift:
        head = f"{head} {product.display}"
    head = _fit(head, MAX_TITLE_LEN)

    # Every word the title has used, and how often.
    used: dict[str, int] = {}
    for token in _tokens(head):
        used[_key(token)] = used.get(_key(token), 0) + 1
    nouns_used: dict[str, int] = {}
    if product.display:
        nouns_used[_noun_word(product.display)] = 1

    candidates = [p for p in phrases if _allowed(p, product)]
    elsewhere = {id(p) for p in _other_products(phrases, candidates, product)}
    # A phrase that names no product and is used far less often than this product's own
    # noun comes from a corner of the market (the prints in a mug search): "Gallery Wall".
    ours = _family_count(phrases, report, product.family)
    minor = {id(p) for p in candidates if p.family is None and p.count < 0.25 * ours}
    chosen: list[_Phrase] = []
    segments: list[str] = []
    length = len(head)
    echoes = 0  # phrases that brought a concept word back
    while len(segments) < MAX_TITLE_SEGMENTS - 1:
        best: tuple[tuple[float, int, str], _Phrase, str, bool] | None = None
        for phrase in candidates:
            if any(phrase is c for c in chosen) or id(phrase) in elsewhere | minor:
                continue
            novelty, new = phrase.novelty(used)
            if novelty < 1:
                continue
            # One word of the concept may come back, once per phrase and in at most two
            # phrases: "Pickleball Queen Shirt, Pickleball Lover Gift" reads naturally,
            # "Mountain Sunset Tee" after "Retro Mountain Sunset Shirt" does not. "Gift"
            # may come back once, like a product noun: "Nature Lover Gift, Hiker Gift"
            # are two searches.
            repeats = {k for k in phrase.words if k in used
                       and not (k in _HEADS and used[k] < 2)}
            if repeats and (
                len(repeats) > 1
                or echoes >= MAX_ECHOES
                or not repeats <= concept_keys
                or any(used[k] > 1 for k in repeats)
                or repeats & (_GENERIC | _AUDIENCE)
            ):
                continue
            if _spans(phrase, concept_last):
                continue
            if phrase.audience and any(k in _AUDIENCE for k in used):
                continue
            words = [_noun_word(shown) for _s, _e, family, shown in phrase.nouns
                     if family == product.family]
            if any(nouns_used.get(w, 0) + words.count(w) > 2 for w in words):
                continue
            if sum(nouns_used.values()) + len(words) > MAX_TITLE_NOUNS:
                continue
            shown = _phrase_display(phrase.tokens)
            if length + 2 + len(shown) > MAX_TITLE_LEN:
                continue
            score = _share(phrase.count, report)
            score *= 1 + 0.4 * (novelty - 1)
            if new and new <= _WEAK:
                score *= 0.5
            if len(phrase.tokens) >= 3:
                score *= 1.25
            if phrase.family is None and not (set(phrase.keys) & _HEADS):
                score *= 0.8
            if repeats:
                score *= 0.85
            if phrase.tagged:
                score *= 1.1
            rank = (score, phrase.count, phrase.text)
            if best is None or rank > best[0]:
                best = (rank, phrase, shown, bool(repeats))
        if best is None:
            break
        _rank, phrase, shown, echoed = best
        echoes += echoed
        chosen.append(phrase)
        segments.append(shown)
        length += 2 + len(shown)
        for token in phrase.tokens:
            used[_key(token)] = used.get(_key(token), 0) + 1
        for _s, _e, family, noun in phrase.nouns:
            if family == product.family:
                nouns_used[_noun_word(noun)] = nouns_used.get(_noun_word(noun), 0) + 1

    # The audience goes last, the way a buyer reads it: "…, Camping Shirt for Men and Women".
    order = sorted(range(len(segments)), key=lambda i: bool(chosen[i].audience))
    chosen = [chosen[i] for i in order]
    segments = [segments[i] for i in order]
    _add_audience(segments, chosen, product, report, used, length)
    usable = [p for p in candidates
              if id(p) not in elsewhere and not _spans(p, concept_last)]
    return _Plan(head, segments, chosen, usable, product, concept_keys)


def _add_audience(segments: list[str], chosen: list[_Phrase], product: _Product,
                  report: MarketReport | None, used: dict[str, int], length: int) -> None:
    """Close with who it is for, when the template or enough of the market says so."""
    if not segments or any(k in _AUDIENCE for k in used):
        return
    sides = set(product.audience)
    if not sides and _has_market(report):
        counts = _word_counts(report)
        for phrase in _market_phrases(report):
            for key in phrase.audience:
                counts[key] = max(counts.get(key, 0), phrase.count)
        women = max((counts.get(k, 0) for k in _AUDIENCE_WOMEN), default=0)
        men = max((counts.get(k, 0) for k in _AUDIENCE_MEN), default=0)
        unisex = counts.get("unisex", 0)
        if _share(women, report) >= 0.1:
            sides.add("women")
        if _share(men, report) >= 0.1:
            sides.add("men")
        if product.family in _APPAREL and _share(unisex, report) >= 0.1:
            sides |= {"women", "men"}
    if not sides:
        return
    if product.family in _APPAREL:
        if len(sides) == 2:
            closer = "for Men and Women"
        else:
            closer = "for Women" if "women" in sides else "for Men"
        targets = [i for i, p in enumerate(chosen) if p.family == product.family]
    elif len(sides) == 1:
        closer = "for Her" if "women" in sides else "for Him"
        targets = [i for i, p in enumerate(chosen) if p.tokens[-1] in _HEADS]
    else:
        return
    if not targets or length + 1 + len(closer) > MAX_TITLE_LEN:
        return
    index = targets[-1]
    segments[index] = f"{segments[index]} {closer}"
    # Keep it last: a closer reads as the end of the title.
    segments.append(segments.pop(index))
    chosen.append(chosen.pop(index))


def build_title(seed: Seed, report: MarketReport | None = None, *,
                product_hint: Hint = None) -> str:
    """The concept and product first, then 3-4 market phrases that each add a new search.

    `product_hint` is the seller's own text about the product (see `hint_from`): the
    template listing's title first, then its tags and description. It names the product
    when the file name does not, and it is the only thing that lets a size, a material
    or a brand into the title. Never exceeds 140 characters, and only uses characters
    Etsy accepts.
    """
    plan = _plan(seed, report, product_hint)
    title = ", ".join([plan.head, *plan.segments])
    return _fit(_title_safe(title), MAX_TITLE_LEN).strip(" ,-|/")


# --- tags ---------------------------------------------------------------------------


def _bag(tag: str) -> frozenset[str]:
    """A tag as the set of words a search matches, with shirt/tee/t-shirt as one.

    "canvas tote" and "canvas tote bag" are one search; "iphone case" is its own.
    """
    tokens = _tokens(tag)
    keys: set[str] = set()
    covered: set[int] = set()
    for start, end, family, shown in _nouns(tokens):
        keys.add("@" + (shown.lower() if shown in _OWN_SEARCH else family))
        covered.update(range(start, end))
    keys |= {_key(t) for i, t in enumerate(tokens) if i not in covered}
    return frozenset(k for k in keys if k not in STOPWORDS)


def _too_similar(candidate: str, existing: list[str]) -> bool:
    """Catch pairs that would burn two of the thirteen slots on one search.

    'gift'/'gifts', 'mountain shirt'/'mountain t-shirt', 'retro sunset'/'sunset retro',
    and a single word that another tag already contains.
    """
    squashed = candidate.replace(" ", "").replace("-", "").rstrip("s")
    bag = _bag(candidate)
    single = len(_tokens(candidate)) == 1
    for other in existing:
        if candidate == other:
            return True
        if squashed == other.replace(" ", "").replace("-", "").rstrip("s"):
            return True
        other_bag = _bag(other)
        if bag and (bag == other_bag or (single and bag <= other_bag)):
            return True
    return False


def _tag_pieces(phrase: _Phrase) -> list[str]:
    """Shorter tags inside a phrase too long for one: its last three or two words.

    The end keeps the product ("leaf phone case", "paw print shirt"); a cut never splits
    a product noun ("phone | case") and never leaves half a phrase ("lover gift").
    """
    tokens = phrase.tokens
    inside = {i for start, end, *_ in phrase.nouns for i in range(start + 1, end)}
    pieces = []
    for size in (3, 2):
        start = len(tokens) - size
        if start <= 0 or start in inside:
            continue
        piece = _Phrase(tokens[start:], phrase.count)
        if not piece.broken and piece.meaningful() and len(piece.text) <= MAX_TAG_LEN:
            pieces.append(piece.text)
    return pieces


def build_tags(seed: Seed, report: MarketReport | None = None, *,
               product_hint: Hint = None) -> list[str]:
    """Thirteen tags at most, each within Etsy's length and character rules.

    Order of preference: the concept; the market's own tags that carry a concept word;
    the concept with the product; the title's phrases; the market's other tags and
    phrases; generic ones ("gift for her"); single words last — they compete with the
    whole marketplace, so they are the least valuable thing to spend a slot on.
    """
    plan = _plan(seed, report, product_hint)
    product = plan.product
    tags: list[str] = []

    def add(raw: str) -> None:
        if len(tags) >= MAX_TAGS:
            return
        tag = clean_tag(raw)
        if tag and not _too_similar(tag, tags):
            tags.append(tag)

    def generic(phrase: _Phrase) -> bool:
        return not phrase.meaningful()

    add(seed.text)

    tagged = sorted((p for p in plan.phrases if p.tagged), key=lambda p: (-p.count, p.text))
    rooted = [p for p in tagged if set(p.keys) & plan.concept_keys][:4]
    for phrase in rooted:
        add(phrase.text)

    # The concept with the product, when the market's own tags did not say it: the whole
    # concept, or its opening pair ("black cat mug", "national park print"). A single word
    # of it can mislead ("black mug"), and a later pair rarely reads ("mom ever mug").
    noun = ""
    if product.family:
        noun = _TAG_NOUNS.get(product.display, product.display.lower())
    words = [w for w in seed.words if w not in STOPWORDS]
    if noun and not product.in_concept and len(rooted) < 3:
        add(f"{seed.text} {noun}")
        if len(words) > 2:
            add(f"{words[0]} {words[1]} {noun}")

    for phrase in plan.chosen:
        add(phrase.text)
    for phrase in tagged:
        if not generic(phrase):
            add(phrase.text)
    untagged = sorted((p for p in plan.phrases if not p.tagged), key=lambda p: (-p.count, p.text))
    for phrase in untagged:
        if not generic(phrase):
            add(phrase.text)
    # A phrase too long for a tag still holds a shorter search, and research keeps only
    # the long one: "monstera leaf phone case" (24 characters) gives "leaf phone case".
    for phrase in untagged:
        if len(phrase.text) > MAX_TAG_LEN and not generic(phrase):
            for piece in _tag_pieces(phrase):
                before = len(tags)
                add(piece)
                if len(tags) > before:
                    break
    for phrase in tagged:
        if generic(phrase):
            add(phrase.text)

    for index in range(len(words) - 1):
        add(f"{words[index]} {words[index + 1]}")
    for word in words:
        add(word)
    if noun:
        add(noun)
    return tags[:MAX_TAGS]


def fill_tags(tags: list[str], extra: Sequence[str]) -> int:
    """Add `extra` tags to the free slots, skipping near-duplicates; how many were added."""
    added = 0
    for raw in extra:
        if len(tags) >= MAX_TAGS:
            break
        tag = clean_tag(raw)
        if tag and not _too_similar(tag, tags):
            tags.append(tag)
            added += 1
    return added


def build_description(seed: Seed, template_description: str, title: str) -> str:
    """The template's own description, with the concept named in the opening line.

    A description is prose. It cannot be measured out of n-grams, and inventing one
    would be the tool writing marketing copy it has no basis for. So the seller's own
    wording carries over, and only the first line is specific to this product.
    """
    body = (template_description or "").strip()
    opening = f"{title}."
    if not body:
        return opening
    if body.lower().startswith(title.lower()[:40]):
        return body
    return f"{opening}\n\n{body}"


def generate(
    seed: Seed,
    report: MarketReport | None = None,
    *,
    template_description: str = "",
    fallback_tags: list[str] | None = None,
    template_title: str = "",
) -> Generated:
    """Produce the copy for one product, and say honestly how much evidence backed it.

    The template's title, tags and description tell the builders what the product is
    and which claims about it (size, material, brand) are the seller's own.
    """
    warnings: list[str] = []
    sources: list[str] = []

    if not seed:
        # Nothing usable came out of the filename. Refusing beats inventing a
        # confident title and putting the wrong listing in someone's shop.
        return Generated(
            title="",
            tags=[],
            warnings=[seed.reason or "no product concept could be derived from the filename"],
        )

    hint = hint_from(template_title, fallback_tags, template_description)
    # What was searched: the concept, with the product when it does not say it.
    searched = str(getattr(report, "keyword", "") or "") or research_keyword(seed, hint)
    if report and not report.empty:
        sources.append(f"{report.sampled} listings ranking for {searched!r}")
        if report.sampled < 20:
            warnings.append(
                f"only {report.sampled} listing(s) rank for {searched!r} — too thin a "
                "sample to draw tags from, so this is mostly your own words"
            )
    else:
        warnings.append(
            f"no market data for {searched!r}; tags come from the filename and your "
            "template only"
        )

    title = build_title(seed, report, product_hint=hint)
    tags = build_tags(seed, report, product_hint=hint)

    # Free slots take the template's tags that suit any design of its product, never
    # the ones about its own design (product_tags).
    if fill_tags(tags, product_tags(fallback_tags, template_title, seed)):
        sources.append("your template listing's tags")

    if len(tags) < MAX_TAGS:
        warnings.append(f"{len(tags)}/{MAX_TAGS} tags — the rest could not be filled honestly")

    return Generated(
        title=title,
        tags=tags,
        description=build_description(seed, template_description, title),
        sources=sources,
        warnings=warnings,
    )
