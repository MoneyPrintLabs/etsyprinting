"""Test isolation.

`Config.load()` reads a `.env` from the working directory. Without this, a developer
who has actually configured stallkit gets different results from CI — their real
credentials leak into tests that were written assuming none exist. That is a test
suite that passes for the wrong reason, and it hides exactly the bugs these tests are
meant to catch.

So every test starts in an empty directory with no Etsy environment. Tests that need
repository files address them by absolute path.
"""

import pytest

ETSY_VARS = (
    "ETSY_KEYSTRING",
    "ETSY_SHARED_SECRET",
    "ETSY_REDIRECT_URI",
    "ETSY_SHOP_ID",
    "ETSY_SCOPES",
    "STALLKIT_RATE_PER_SEC",
    "STALLKIT_HOME",
)


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch, tmp_path):
    """No inherited credentials, no inherited .env, no writing to a real token store."""
    for name in ETSY_VARS:
        monkeypatch.delenv(name, raising=False)

    home = tmp_path / "stallkit-home"
    home.mkdir()
    monkeypatch.setenv("STALLKIT_HOME", str(home))

    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
