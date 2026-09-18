"""The service layer must stay callable from something other than a browser.

Services are the shared core beneath the route handlers. If one of them flashes
a message or returns a redirect, it has quietly become HTML-only: a JSON
endpoint for a React page or a mobile app could not reuse it, and the rule it
implements would end up duplicated -- which is exactly how two copies of a
billing rule drift apart.

These checks are structural, so they hold even for code paths no test exercises.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVICES = REPO / "aeronautics_members" / "services"

# Presentation and request-scoped names that belong in a route, not a service.
FORBIDDEN = {
    "flash": "flashing a message is presentation; return a result and let the route flash",
    "redirect": "deciding where a browser goes next is the route's job",
    "render_template": "rendering HTML is presentation; return data instead",
    "request": "reading the HTTP request couples the service to one transport",
    "session": "the browser session is request state; pass what you need as an argument",
    "abort": "raise a ServiceError subclass so a JSON caller can handle it too",
}


def _service_modules():
    return sorted(p for p in SERVICES.rglob("*.py") if p.name != "__init__.py")


def test_services_exist():
    # Guards against this file silently passing because nothing was extracted yet.
    assert _service_modules(), "no service modules found; extraction not started?"


def test_services_do_not_use_presentation_helpers():
    offenders = []
    for path in _service_modules():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            # Imported by name: `from flask import flash`
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in FORBIDDEN:
                        offenders.append(
                            f"{path.name}:{node.lineno} imports {alias.name!r} "
                            f"-- {FORBIDDEN[alias.name]}"
                        )
            # Called directly: `flash(...)`, or attribute use like `request.form`
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN:
                offenders.append(
                    f"{path.name}:{node.lineno} uses {node.id!r} "
                    f"-- {FORBIDDEN[node.id]}"
                )

    assert not offenders, (
        "Service modules must not depend on HTTP/HTML presentation:\n  "
        + "\n  ".join(offenders)
    )


def test_service_errors_carry_a_code_and_status():
    """A JSON caller needs a stable code; an HTML caller needs the message."""
    from aeronautics_members.services import (
        ConflictError,
        ExternalServiceError,
        NotFoundError,
        ServiceError,
        ValidationError,
    )

    for cls in (ValidationError, NotFoundError, ConflictError, ExternalServiceError):
        err = cls("something went wrong")
        assert issubclass(cls, ServiceError)
        assert err.code and err.code != "service_error", f"{cls.__name__} needs its own code"
        assert 400 <= err.http_status <= 599
        assert err.to_dict()["error"]["message"] == "something went wrong"

    detailed = ValidationError("bad field", details={"field": "email"})
    assert detailed.to_dict()["error"]["details"] == {"field": "email"}
