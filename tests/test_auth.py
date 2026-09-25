import pytest

from stallkit.auth import (
    Token,
    build_authorization_url,
    extract_code,
    is_loopback,
    validate_redirect_uri,
)
from stallkit.config import Config
from stallkit.errors import AuthError


def cfg(**kw):
    base = {
        "keystring": "testkey",
        "redirect_uri": "https://example.com/etsy-callback",
        "scopes": ("shops_r", "listings_r"),
    }
    base.update(kw)
    return Config(**base)


# --- redirect URI rules --------------------------------------------------------
# Etsy's app settings screen states these verbatim:
#   Must start with http:// or https://
#   Host must be a domain name (e.g. example.com)
#   IP addresses are not allowed (e.g. 127.0.0.1)
# The prose docs say the callback must use https; the settings screen is narrower and
# is what Etsy actually enforces. These tests pin the enforced rules.


def test_https_redirect_is_accepted():
    validate_redirect_uri("https://example.com/etsy-callback")


def test_plain_http_is_accepted():
    validate_redirect_uri("http://example.com/cb")


def test_http_localhost_is_accepted():
    # 'localhost' is a domain name, not an IP — so a loopback listener does work.
    validate_redirect_uri("http://localhost:3003/oauth/redirect")


def test_ipv4_literal_is_rejected():
    with pytest.raises(AuthError, match="IP addresses"):
        validate_redirect_uri("http://127.0.0.1:3003/oauth/redirect")


def test_ipv6_literal_is_rejected():
    with pytest.raises(AuthError, match="IP addresses"):
        validate_redirect_uri("http://[::1]:3003/cb")


def test_a_non_web_scheme_is_rejected():
    with pytest.raises(AuthError, match="http:// or https://"):
        validate_redirect_uri("ftp://example.com/cb")


def test_empty_redirect_explains_itself():
    with pytest.raises(AuthError, match="ETSY_REDIRECT_URI is not set"):
        validate_redirect_uri("")


def test_non_url_is_rejected():
    with pytest.raises(AuthError, match="not a full URL"):
        validate_redirect_uri("https:///nohost")


def test_is_loopback_detects_a_locally_servable_callback():
    assert is_loopback("http://localhost:3001/oauth/etsy/callback")
    assert not is_loopback("https://example.com/etsy-callback")


# --- the authorization URL -----------------------------------------------------


def test_authorization_url_carries_pkce_and_state():
    request = build_authorization_url(cfg())
    assert request.url.startswith("https://www.etsy.com/oauth/connect?")
    # Etsy accepts no challenge method other than S256.
    assert "code_challenge_method=S256" in request.url
    assert "response_type=code" in request.url
    assert "client_id=testkey" in request.url
    assert f"state={request.state}" in request.url
    assert request.verifier and request.verifier != request.state


def test_authorization_url_refuses_an_ip_callback():
    with pytest.raises(AuthError, match="IP addresses"):
        build_authorization_url(cfg(redirect_uri="http://127.0.0.1:3003/cb"))


def test_authorization_url_accepts_a_localhost_callback():
    request = build_authorization_url(cfg(redirect_uri="http://localhost:3003/cb"))
    assert "code_challenge_method=S256" in request.url


# --- reading the code back -----------------------------------------------------


def test_extract_code_from_full_url():
    url = "https://example.com/etsy-callback?code=abc123&state=xyz"
    assert extract_code(url, expected_state="xyz") == "abc123"


def test_extract_code_from_bare_code():
    assert extract_code("  abc123  ") == "abc123"


def test_extract_code_strips_surrounding_quotes():
    assert extract_code('"https://example.com/cb?code=abc123"') == "abc123"


def test_extract_code_rejects_state_mismatch():
    url = "https://example.com/cb?code=abc123&state=attacker"
    with pytest.raises(AuthError, match="state value does not match"):
        extract_code(url, expected_state="mine")


def test_extract_code_allows_url_without_state_param():
    # Some setups strip query params; the code is still usable.
    assert extract_code("https://example.com/cb?code=abc123", expected_state="mine") == "abc123"


def test_extract_code_surfaces_etsy_error():
    url = "https://example.com/cb?error=access_denied&error_description=User+said+no"
    with pytest.raises(AuthError, match="access_denied"):
        extract_code(url)


def test_extract_code_rejects_url_without_code():
    with pytest.raises(AuthError, match="no `code=` parameter"):
        extract_code("https://example.com/cb?foo=bar")


def test_extract_code_rejects_a_pasted_sentence():
    with pytest.raises(AuthError, match="does not look like"):
        extract_code("I could not find the code")


def test_extract_code_rejects_empty():
    with pytest.raises(AuthError, match="Nothing pasted"):
        extract_code("   ")


# --- token bookkeeping ---------------------------------------------------------


def test_user_id_is_read_from_the_token_prefix():
    token = Token(access_token="12345.abcdef", refresh_token="r", expires_at=0)
    assert token.user_id == 12345


def test_user_id_is_none_for_an_unexpected_shape():
    assert Token(access_token="nodigits", refresh_token="r", expires_at=0).user_id is None


def test_token_counts_as_expired_inside_the_skew_window():
    import time

    # 60s of life left, but the 120s skew means we refresh rather than risk it.
    token = Token(access_token="1.a", refresh_token="r", expires_at=time.time() + 60)
    assert token.expired


def test_token_with_ample_life_is_not_expired():
    import time

    token = Token(access_token="1.a", refresh_token="r", expires_at=time.time() + 3600)
    assert not token.expired
    assert token.seconds_left > 3000


def test_token_round_trips_through_a_dict():
    token = Token(access_token="1.a", refresh_token="r", expires_at=99.0, scopes=("shops_r",))
    assert Token.from_dict(token.to_dict()) == token


def test_from_response_rejects_a_payload_missing_the_refresh_token():
    with pytest.raises(AuthError, match="refresh_token"):
        Token.from_response({"access_token": "1.a", "expires_in": 3600}, ())
