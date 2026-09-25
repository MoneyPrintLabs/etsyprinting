"""Exception types. Every error surfaced to the CLI derives from StallKitError."""

from __future__ import annotations


class StallKitError(Exception):
    """Base class. The CLI prints these as clean messages instead of tracebacks."""


class ConfigError(StallKitError):
    """Missing or invalid configuration (no API key, bad .env, unwritable home)."""


class AuthError(StallKitError):
    """Not authenticated, token refresh failed, or OAuth flow aborted."""


class ValidationError(StallKitError):
    """Input failed local validation before any network call was made."""


class EtsyApiError(StallKitError):
    """A non-2xx response from the Etsy API."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        method: str = "",
        path: str = "",
        body: str = "",
    ) -> None:
        self.status = status
        self.message = message
        self.method = method
        self.path = path
        self.body = body
        where = f" ({method} {path})" if path else ""
        super().__init__(f"Etsy API {status}{where}: {message}")

    @property
    def is_retryable(self) -> bool:
        return self.status == 429 or self.status >= 500

    def hint(self) -> str:
        """A human-readable next step, since Etsy's own error strings are terse."""
        if self.status == 401:
            return "Token expired or revoked. Run: stallkit auth login"
        if self.status == 403:
            return (
                "Forbidden. Usually a missing OAuth scope, or the shop_id does not "
                "belong to the authenticated user. Check: stallkit auth status"
            )
        if self.status == 404:
            return "Not found. Verify the shop_id / listing_id / receipt_id."
        if self.status == 429:
            return (
                "Rate limited. stallkit throttles automatically; lower it with "
                "STALLKIT_RATE_PER_SEC (default 4) if this persists."
            )
        if self.status >= 500:
            return "Etsy-side error. Safe to retry in a few minutes."
        if self.status == 400:
            return (
                "Etsy rejected the payload. Common causes: taxonomy_id invalid for the "
                "shop, missing shipping_profile_id on a physical listing, a tag over 20 "
                "characters, or more than 13 tags."
            )
        return ""
