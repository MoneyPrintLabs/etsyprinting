"""A disk cache for market research.

A hundred designs usually cover far fewer distinct concepts, and the same concepts
come back on the next run. Without a cache a batch would spend its request budget
asking Etsy the same question repeatedly — which matters on a Personal Access app
allowed 5 requests a second and 5,000 a day.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ..config import cache_dir

DEFAULT_TTL_SECONDS = 7 * 24 * 3600


def _slot(key: str, namespace: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return cache_dir() / namespace / f"{digest}.json"


def load(key: str, *, namespace: str = "research", ttl: int = DEFAULT_TTL_SECONDS) -> Any | None:
    """Return the cached value, or None when it is missing, stale or unreadable."""
    path = _slot(key, namespace)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stored_at = payload.get("stored_at")
    if not isinstance(stored_at, (int, float)):
        return None
    if ttl and time.time() - stored_at > ttl:
        return None
    if payload.get("key") != key:
        return None  # hash collision, or a hand-edited file
    return payload.get("value")


def store(key: str, value: Any, *, namespace: str = "research") -> None:
    """Best effort. A cache that cannot be written must never break a run."""
    path = _slot(key, namespace)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"key": key, "stored_at": time.time(), "value": value}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def clear(*, namespace: str = "research") -> int:
    folder = cache_dir() / namespace
    if not folder.is_dir():
        return 0
    removed = 0
    for path in folder.glob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
