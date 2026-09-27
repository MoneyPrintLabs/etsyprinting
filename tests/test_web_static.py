"""The contract between the server and the web UI's static files.

Checked without a browser: every error code the server can send has words in the
UI, every route of the app has its page module, strings and stylesheet, and every
relative import in the JavaScript points at a file that exists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from stallkit.web.server import STATIC_DIR

WEB_DIR = Path(STATIC_DIR).parent
JS_DIR = STATIC_DIR / "js"
I18N_DIR = STATIC_DIR / "i18n"

pytestmark = pytest.mark.skipif(not (JS_DIR / "app.js").is_file(), reason="no web UI yet")

# ApiError(409, "busy", ...) and the server's own refusals, _reject(403, "forbidden", ...).
_ERROR_CODE = re.compile(r"""(?:ApiError|_reject)\(\s*\d+\s*,\s*["']([a-z_]+)["']""")
# Codes the browser makes up itself (api.js): a failed fetch.
CLIENT_CODES = {"network"}


def _backend_error_codes() -> set[str]:
    codes: set[str] = set()
    for path in WEB_DIR.rglob("*.py"):
        codes.update(_ERROR_CODE.findall(path.read_text(encoding="utf-8")))
    return codes | CLIENT_CODES


def _all_translated_keys() -> set[str]:
    keys: set[str] = set()
    for path in I18N_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        keys.update(data.get("tr", {}))
    return keys


def test_every_error_code_the_server_sends_has_words():
    """The UI shows errors.<code>: from common.json, or from the page's own strings
    for a code only that page's endpoints send."""
    codes = _backend_error_codes()
    assert {"busy", "no_session", "not_found", "internal"} <= codes  # the scan works
    keys = _all_translated_keys()
    missing = sorted(code for code in codes if f"errors.{code}" not in keys)
    assert not missing, f"add errors.<code> strings for: {missing}"


def test_the_spec_error_codes_are_in_common():
    common = json.loads((I18N_DIR / "common.json").read_text(encoding="utf-8"))
    for code in ("no_session", "setup_needed", "offline", "reconnect", "bad_keys", "invalid",
                 "not_found", "rate_limited", "etsy_error", "tracking_restricted", "busy",
                 "internal", "network"):
        assert f"errors.{code}" in common["tr"], code


def _route_pages() -> list[str]:
    app = (JS_DIR / "app.js").read_text(encoding="utf-8")
    table = app.split("export const ROUTES", 1)[1].split("];", 1)[0]
    return sorted(set(re.findall(r"""page:\s*["']([a-z-]+)["']""", table)))


def test_every_route_has_its_page_files():
    pages = _route_pages()
    assert "panel" in pages and "listing-detail" in pages
    for page in pages:
        assert (JS_DIR / "pages" / f"{page}.js").is_file(), page
        assert (I18N_DIR / f"{page}.json").is_file(), page
        assert (STATIC_DIR / "css" / "pages" / f"{page}.css").is_file(), page
        module = (JS_DIR / "pages" / f"{page}.js").read_text(encoding="utf-8")
        assert "export default" in module and "mount" in module, page


_IMPORT = re.compile(
    r"""(?:^|[;\s])(?:import|export)\s[^"'`;]*?from\s*["']([^"']+)["']"""
    r"""|(?:^|[;\s])import\s*["']([^"']+)["']"""
    r"""|\bimport\(\s*["']([^"']+)["']\s*\)""",
    re.MULTILINE,
)


@pytest.mark.parametrize(
    "path",
    sorted(JS_DIR.rglob("*.js")) if JS_DIR.is_dir() else [],
    ids=lambda p: p.relative_to(JS_DIR).as_posix(),
)
def test_every_relative_import_points_at_a_file(path):
    for groups in _IMPORT.findall(path.read_text(encoding="utf-8")):
        target = next(g for g in groups if g)
        if target.startswith("/"):
            resolved = STATIC_DIR / target.lstrip("/")
        elif target.startswith("."):
            resolved = (path.parent / target).resolve()
        else:
            pytest.fail(f"{path.name}: bare import {target!r} (no npm, no CDN)")
        assert resolved.is_file(), f"{path.name} imports {target}, which does not exist"
