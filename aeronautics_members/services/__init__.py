"""Application services: what the association actually does.

This layer sits *below* the route handlers. A service function takes plain
arguments and returns a plain result; it does not know whether a browser form, a
Stripe webhook, a CLI command, a scheduled job -- or, later, a JSON endpoint
backing a React page or a mobile app -- called it.

The contract, enforced by ``tests/test_service_layer_contract.py``:

* No ``flash``, ``redirect``, ``render_template``, ``request``, ``session`` or
  ``abort``. Those are HTTP/HTML concerns and belong in the route.
* No ``url_for`` for user-facing navigation. Building an absolute link to email
  someone is fine (``build_public_url``); deciding where a browser goes next is
  the route's job.
* Failures are raised as ``ServiceError`` subclasses carrying a machine-readable
  ``code`` and a human message, not as ``abort(400)``. The HTML route turns one
  into a flash plus a redirect; a JSON endpoint turns the same error into a
  status code and a body, without either duplicating the rule.

Using ``current_app.logger`` and ``current_app.config`` is allowed: they are
ambient application context, not request or presentation state.
"""


class ServiceError(Exception):
    """A domain failure a caller is expected to handle and present.

    ``code`` is stable and machine-readable (for a JSON client); ``message`` is
    human-readable text suitable for showing to the person who triggered it.
    """

    code = "service_error"
    http_status = 400

    def __init__(self, message, code=None, http_status=None, details=None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        self.details = details or {}

    def to_dict(self):
        """Serialize for a JSON API response."""
        payload = {"error": {"code": self.code, "message": self.message}}
        if self.details:
            payload["error"]["details"] = self.details
        return payload


class ValidationError(ServiceError):
    """Input that the caller can correct and retry."""

    code = "validation_error"
    http_status = 400


class NotFoundError(ServiceError):
    """The addressed object does not exist (or is not visible to the caller)."""

    code = "not_found"
    http_status = 404


class PermissionError_(ServiceError):
    """The caller is authenticated but not allowed to do this."""

    code = "forbidden"
    http_status = 403


class ConflictError(ServiceError):
    """The request is valid but conflicts with current state."""

    code = "conflict"
    http_status = 409


class ExternalServiceError(ServiceError):
    """A dependency (Stripe, Discourse, SMTP) failed in a way we cannot fix here."""

    code = "external_service_unavailable"
    http_status = 502
