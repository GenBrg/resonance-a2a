"""Problem+JSON error envelope.

Every failure the client can observe carries a stable machine code so the SPA and
the MCP bridge never have to parse prose.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str = "", *, code: str | None = None, details: Any = None):
        super().__init__(message or self.code)
        self.message = message or self.code
        if code:
            self.code = code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.details is not None:
            body["error"]["details"] = self.details
        return body


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class QuotaExceeded(AppError):
    status_code = 429
    code = "attention_budget_exhausted"


class PrivacyViolation(AppError):
    """Raised when an operation would break a privacy invariant (k-anonymity, transcripts)."""

    status_code = 422
    code = "privacy_invariant_violation"


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"
