"""Where the desktop window keeps what it is told.

Credentials go in the same `.env` the command line reads — `STALLKIT_HOME/.env`,
`~/.stallkit/.env` by default — so a shop set up in the window works from a terminal
and the other way round. Window-only preferences (language, last folder, last CSV)
go beside it in `desktop.json`, which holds nothing secret.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from ..config import home_dir, load_env, write_env_file

ETSY_REDIRECT_DEFAULT = "http://localhost:3003/oauth/redirect"
PINTEREST_REDIRECT_DEFAULT = "http://localhost:8085/"


def env_path() -> Path:
    return home_dir() / ".env"


def prefs_path() -> Path:
    return home_dir() / "desktop.json"


def _file_values() -> dict[str, str]:
    path = env_path()
    if not path.is_file():
        return {}
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def current(key: str, default: str = "") -> str:
    """The value a command would see right now: environment first, then the files."""
    load_env()
    return (os.environ.get(key) or "").strip() or default


def save(updates: dict[str, str]) -> Path:
    """Merge `updates` into the .env and the running process.

    Keys that are not being changed are kept, including ones the window has no field
    for (ETSY_SHOP_ID, ETSY_SCOPES, a raised rate limit). An empty value removes the
    key. The process environment is updated too: `Config.load()` never overrides a
    variable that is already set, so without this a corrected keystring would only
    take effect after a restart.
    """
    values = _file_values()
    for key, raw in updates.items():
        value = (raw or "").strip()
        if value:
            values[key] = value
            os.environ[key] = value
        else:
            values.pop(key, None)
            os.environ.pop(key, None)
    path = env_path()
    write_env_file(path, values)
    return path


def load_prefs() -> dict[str, Any]:
    try:
        data = json.loads(prefs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(prefs: dict[str, Any]) -> None:
    """Best effort: a preference that fails to save is not worth an error dialog."""
    path = prefs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
