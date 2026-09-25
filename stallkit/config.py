"""Configuration: where credentials live, and the constants pinned from Etsy's OpenAPI spec.

Nothing here is shop-specific. Every user of this tool supplies their own Etsy app
keystring, so the repository ships no secrets and the same code runs for anyone.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .errors import ConfigError

# --- Endpoints, verified against https://www.etsy.com/openapi/generated/oas/3.0.0.json ---

API_BASE = "https://openapi.etsy.com/v3/application"
OAUTH_AUTHORIZE_URL = "https://www.etsy.com/oauth/connect"

# The generated spec lists openapi.etsy.com; Etsy's OAuth guide documents api.etsy.com.
# Both are live. We try them in order so a change on either side does not break login.
OAUTH_TOKEN_URLS = (
    "https://api.etsy.com/v3/public/oauth/token",
    "https://openapi.etsy.com/v3/public/oauth/token",
)

# Least privilege: only what stallkit actually calls. No delete, no profile writes.
#   shops_r        -> /users/me, /shops/{id}         (required even to discover your shop)
#   listings_r/w   -> read + create/update listings and upload images
#   transactions_r -> read receipts (orders)
#   transactions_w -> submit tracking numbers
DEFAULT_SCOPES = (
    "shops_r",
    "listings_r",
    "listings_w",
    "transactions_r",
    "transactions_w",
)

# No default is possible: the callback must match a URL registered on your own app,
# byte for byte. Etsy accepts http and https, requires a domain-name host, and rejects
# IP addresses — so http://localhost:PORT/... is valid and 127.0.0.1 is not.
# See auth.validate_redirect_uri.
DEFAULT_REDIRECT_URI = ""

# Rate limits are PER APP, and your app's actual allowance is printed on its row at
# etsy.com/developers/your-apps. A Personal Access app gets 5 QPS / 5,000 per day —
# not the 10/sec, 10,000/day figure the general docs quote, which applies to apps with
# commercial access. Default below the personal tier so the out-of-the-box setting is
# safe for everyone, and raise it with STALLKIT_RATE_PER_SEC if your app is allowed more.
DEFAULT_RATE_PER_SEC = 4.0

# Etsy listing constraints, enforced locally so we fail before wasting an API call.
MAX_TITLE_LEN = 140
MAX_TAGS = 13
MAX_TAG_LEN = 20
MAX_MATERIALS = 13
MAX_MATERIAL_LEN = 45
MAX_PAGE_LIMIT = 100

# Ten photos per listing, and uploadListingImage ranks them 1-10. The eleventh upload is
# refused *after* the create has already succeeded, which leaves a draft in the shop that
# stallkit holds no delete scope to remove — so the count is checked locally, where it
# costs nothing, instead of being discovered halfway through a batch.
MAX_LISTING_IMAGES = 10

WHO_MADE = ("i_did", "someone_else", "collective")
WHEN_MADE = (
    "made_to_order", "2020_2026", "2010_2019", "2007_2009", "before_2007",
    "2000_2006", "1990s", "1980s", "1970s", "1960s", "1950s", "1940s",
    "1930s", "1920s", "1910s", "1900s", "1800s", "1700s", "before_1700",
)
LISTING_TYPES = ("physical", "download", "both")


def home_dir() -> Path:
    """Directory holding tokens and cache. Override with STALLKIT_HOME."""
    raw = os.environ.get("STALLKIT_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".stallkit"


def token_path() -> Path:
    return home_dir() / "token.json"


def cache_dir() -> Path:
    return home_dir() / "cache"


def load_env() -> None:
    """Load .env from the working directory, then from STALLKIT_HOME.

    Real environment variables always win, so CI and shell exports override files.
    """
    load_dotenv(Path.cwd() / ".env", override=False)
    load_dotenv(home_dir() / ".env", override=False)


@dataclass
class Config:
    keystring: str
    shared_secret: str = ""
    redirect_uri: str = DEFAULT_REDIRECT_URI
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    rate_per_sec: float = DEFAULT_RATE_PER_SEC
    shop_id: int | None = None

    @property
    def api_key_header(self) -> str:
        """The value for the x-api-key header.

        Etsy requires BOTH halves of the app credential here, colon-joined, on every
        v3 request — including the unauthenticated ones. Sending the keystring alone
        gets a 403 whose body literally says
        ``Invalid API key: should be in the format 'keystring:shared_secret'``.

        This is separate from OAuth: PKCE removes the client secret from the *token
        exchange* (client_id stays the bare keystring), but it does not remove the
        shared secret from this header.
        """
        return f"{self.keystring}:{self.shared_secret}"

    @classmethod
    def load(cls, *, require_key: bool = True) -> Config:
        load_env()
        keystring = (os.environ.get("ETSY_KEYSTRING") or "").strip()
        shared_secret = (os.environ.get("ETSY_SHARED_SECRET") or "").strip()

        # Tolerate someone pasting the colon-joined form into ETSY_KEYSTRING. The
        # header wants it joined, but OAuth's client_id must be the bare keystring,
        # so split it back apart rather than sending a broken client_id.
        keystring, shared_secret = split_credential(keystring, shared_secret)

        if require_key and not keystring:
            raise ConfigError(
                "ETSY_KEYSTRING is not set.\n"
                "  1. Create an app at https://www.etsy.com/developers/your-apps\n"
                "  2. Copy .env.example to .env and paste your keystring into it\n"
                "     (or export ETSY_KEYSTRING=... in your shell)"
            )
        if require_key and not shared_secret:
            raise ConfigError(
                "ETSY_SHARED_SECRET is not set.\n"
                "Etsy needs BOTH halves of your app credential in the x-api-key header,\n"
                "joined by a colon — the keystring alone returns 403 on every call.\n"
                "Your Etsy app page shows the shared secret next to the keystring.\n"
                "Add it to .env as ETSY_SHARED_SECRET=..."
            )

        scopes_raw = os.environ.get("ETSY_SCOPES", "").strip()
        scopes = tuple(scopes_raw.split()) if scopes_raw else DEFAULT_SCOPES

        shop_raw = (os.environ.get("ETSY_SHOP_ID") or "").strip()
        shop_id: int | None = None
        if shop_raw:
            try:
                shop_id = int(shop_raw)
            except ValueError as exc:
                raise ConfigError(f"ETSY_SHOP_ID must be a number, got {shop_raw!r}") from exc

        rate_raw = (os.environ.get("STALLKIT_RATE_PER_SEC") or "").strip()
        try:
            rate = float(rate_raw) if rate_raw else DEFAULT_RATE_PER_SEC
        except ValueError as exc:
            raise ConfigError(f"STALLKIT_RATE_PER_SEC must be a number, got {rate_raw!r}") from exc

        return cls(
            keystring=keystring,
            shared_secret=shared_secret,
            redirect_uri=os.environ.get("ETSY_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip(),
            scopes=scopes,
            rate_per_sec=max(0.5, min(rate, 10.0)),
            shop_id=shop_id,
        )


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Write a .env, then narrow permissions — it holds the shared secret."""
    lines = [
        "# Written by `stallkit init`. This file holds credentials: never commit it.",
        "# .gitignore already covers it.",
        "",
    ]
    lines += [f"{key}={value}" for key, value in values.items() if value]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows ignores POSIX modes.


def split_credential(keystring: str, shared_secret: str = "") -> tuple[str, str]:
    """Accept either half separately, or the colon-joined header form in one field."""
    keystring = (keystring or "").strip()
    shared_secret = (shared_secret or "").strip()
    if ":" in keystring:
        head, _, tail = keystring.partition(":")
        keystring = head.strip()
        shared_secret = shared_secret or tail.strip()
    return keystring, shared_secret


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc


def write_json_private(path: Path, data: dict[str, Any]) -> None:
    """Write JSON, then narrow permissions. Tokens are as sensitive as a password."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        # Windows ignores POSIX modes; the file still sits in the user profile.
        pass
