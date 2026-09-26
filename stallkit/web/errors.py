"""Exceptions -> HTTP status and an error code the UI can translate.

Every error the API sends has the shape

    {"error": {"code": "offline", "message": "<English detail>", "params": {...}}}

and the browser shows `errors.<code>` from common.json, falling back to `message`.
Tracebacks never leave the machine: an unexpected exception is logged to the log
file and sent as `internal`.
"""

from __future__ import annotations

import logging
from typing import Any

from ..errors import AuthError, AuthUnreachable, ConfigError, EtsyApiError, ValidationError
from .router import ApiError

log = logging.getLogger("stallkit.web")


def to_api_error(exc: BaseException) -> ApiError:
    """The ApiError an exception is sent as. Logs anything unexpected."""
    if isinstance(exc, ApiError):
        return exc
    from .jobs import JobCancelled

    if isinstance(exc, JobCancelled):
        return ApiError(409, "cancelled", "Cancelled.")
    if isinstance(exc, ConfigError):
        return ApiError(409, "setup_needed", str(exc))
    if isinstance(exc, AuthUnreachable):  # before AuthError: it is a subclass
        return ApiError(503, "offline", str(exc))
    if isinstance(exc, AuthError):
        return ApiError(401, "reconnect", str(exc))
    if isinstance(exc, ValidationError):
        return ApiError(422, "invalid", str(exc))
    if isinstance(exc, EtsyApiError):
        return _etsy(exc)
    if isinstance(exc, FileNotFoundError):
        return ApiError(404, "not_found", "Not found.")
    log.error("Unexpected error", exc_info=(type(exc), exc, exc.__traceback__))
    return ApiError(500, "internal", "Something went wrong inside stallkit. The log file has the details.")


def _etsy(exc: EtsyApiError) -> ApiError:
    status = exc.status
    said = f"{exc.message} {exc.body}".lower()
    if status == 0:
        return ApiError(503, "offline", exc.message)
    if status == 401:
        return ApiError(401, "reconnect", exc.message)
    if status == 403 and "api key" in said:
        return ApiError(403, "bad_keys", exc.message)
    if status == 403 and exc.path.rstrip("/").endswith("/tracking"):
        return ApiError(403, "tracking_restricted", exc.hint() or exc.message)
    if status == 404:
        return ApiError(404, "not_found", exc.message)
    if status == 429:
        return ApiError(429, "rate_limited", exc.message)
    return ApiError(502, "etsy_error", exc.message, status=status)


def describe(exc: BaseException) -> dict[str, Any]:
    """{code, message, params} for a job's `error` field."""
    error = to_api_error(exc)
    return {"code": error.code, "message": error.message, "params": error.params}
