"""Building a title and thirteen tags from a concept and what actually ranks.

There is no search volume to work from — Etsy publishes none — so this does the only
honest thing available: it looks at the listings Etsy returns for the concept and
reuses the vocabulary they share. That is a measurement, not a prediction, and the
interface says so wherever these numbers appear.

Everything here is deliberately deterministic. The same design and the same market
sample produce the same title twice, which is what makes a hundred-product batch
reviewable: a seller who checks ten rows has learned something about the other ninety.

The hard limits are enforced at the point of construction, not checked afterwards, so
this module cannot emit something `listings.build_payload` would reject.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..config import MAX_TAG_LEN, MAX_TAGS, MAX_TITLE_LEN
from ..listings import bad_tag_chars
from ..seo import content_words
from .seeds import Seed

if TYPE_CHECKING:  # pragma: no cover
    from ..seo import MarketReport

# Etsy truncates a title on a mobile card around here, so the concept must land inside
# it. Everything after is for the search index, not the buyer's eye.
MAX_TITLE_SEGMENTS = 5


@dataclass
class Generated:
    title: str
    tags: list[str]
    description: str = ""
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def titlecase(text: str) -> str:
    """Capitalise each word without str.title()'s habit of mangling apostrophes."""
    return " ".join(w[:1].upper() + w[1:] if w else w for w in text.split())


def clean_tag(raw: str) -> str:
    """Strip a tag down to something Etsy will accept, or return '' if nothing is left."""
    text = " ".join(str(raw or "").split()).strip().lower()
    if not text:
        return ""
    bad = bad_tag_chars(text)
    if bad:
        text = " ".join("".join(ch for ch in text if ch not in bad).split())
    return text if len(text) <= MAX_TAG_LEN else ""


def _too_similar(candidate: str, existing: list[str]) -> bool:
    """Catch pairs like 'gift'/'gifts' that would burn two of the thirteen slots."""
    squashed = candidate.replace(" ", "").rstrip("s")
    for other in existing:
        if candidate == other:
            return True
        if squashed == other.replace(" ", "").rstrip("s"):
            return True
    return False


def build_tags(seed: Seed, report: MarketReport | None = None) -> list[str]:
    """Thirteen tags at most, each within Etsy's length and character rules.

    Order of preference: the concept itself, then word pairs from it, then the tags
    most common among the listings that rank for it.
    """
    tags: list[str] = []

    def add(raw: str) -> None:
        if len(tags) >= MAX_TAGS:
            return
        tag = clean_tag(raw)
        if tag and not _too_similar(tag, tags):
            tags.append(tag)

    add(seed.text)
    words = seed.words
    for index in range(len(words) - 1):
        add(f"{words[index]} {words[index + 1]}")

    if report:
        for tag, _count in report.tags:
            add(tag)

    # Single words last: they compete with the whole marketplace, so they are the
    # least valuable thing to spend a slot on.
    for word in words:
        add(word)

    return tags[:MAX_TAGS]


def build_title(seed: Seed, report: MarketReport | None = None) -> str:
    """A title that keeps the concept inside the visible window and never exceeds 140."""
    head = titlecase(seed.text)[:MAX_TITLE_LEN]
    segments = [head]
    covered = set(content_words(seed.text))

    if report:
        for phrase, _count in report.phrases:
            if len(segments) >= MAX_TITLE_SEGMENTS:
                break
            phrase_words = set(content_words(phrase))
            # A phrase that adds no new word adds no new query.
            if not phrase_words or phrase_words <= covered:
                continue
            candidate = ", ".join([*segments, titlecase(phrase)])
            if len(candidate) > MAX_TITLE_LEN:
                continue
            segments.append(titlecase(phrase))
            covered |= phrase_words

    title = ", ".join(segments)
    return title[:MAX_TITLE_LEN].rstrip(" ,")


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
) -> Generated:
    """Produce the copy for one product, and say honestly how much evidence backed it."""
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

    if report and not report.empty:
        sources.append(f"{report.sampled} listings ranking for {seed.text!r}")
        if report.sampled < 20:
            warnings.append(
                f"only {report.sampled} listing(s) rank for {seed.text!r} — too thin a "
                "sample to draw tags from, so this is mostly your own words"
            )
    else:
        warnings.append(
            f"no market data for {seed.text!r}; tags come from the filename and your "
            "template only"
        )

    title = build_title(seed, report)
    tags = build_tags(seed, report)

    if len(tags) < MAX_TAGS and fallback_tags:
        for tag in fallback_tags:
            if len(tags) >= MAX_TAGS:
                break
            cleaned = clean_tag(tag)
            if cleaned and not _too_similar(cleaned, tags):
                tags.append(cleaned)
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
