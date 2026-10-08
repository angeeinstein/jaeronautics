"""The JSON API's plumbing: how an endpoint is declared, checked and answered.

Every endpoint is one function under ``@endpoint(...)``, which says in one
place what it takes and what it returns:

    @endpoint("GET", "/admin/accounts/<int:user_id>", response=AccountOut,
              permissions=[Permission.ACCOUNTS_VIEW], tag="Admin")
    def account(user_id): ...

and the decorator does the rest, the same way for every endpoint:

- **Signed in:** required unless ``public=True``. Not signed in is ``401``
  with JSON -- never the redirect to the login page the pages get.
- **Permissions:** all of them, checked on the server; ``403`` otherwise.
- **Input:** ``body`` (JSON) and ``query`` (the query string) are Pydantic
  models; what does not fit is ``400`` with a message per field, before the
  function runs. The function receives the parsed models.
- **Files:** ``uploads`` names the files a ``multipart/form-data`` request
  carries, each required or not (``{"german": True}``); the function receives
  them as ``files`` -- a file, or ``None`` for one not chosen.
- **Answer:** the function returns an instance of ``response`` (sent as JSON),
  ``None`` (``204``), or -- with ``produces``, the media type -- a Flask
  response with a file.

Errors are always ``{"error": {"code", "message", "fields"?, "details"?}}``:
``code`` is stable for the front end to act on, ``message`` is for people.
The services' own errors (ValidationError, NotFoundError, ...) already carry
both and a status, and come out the same way.

Names stay as the database and services have them: snake_case, in the JSON
too, so one field has one name everywhere. Times are UTC with an explicit
offset; days are ``YYYY-MM-DD``.

The declarations are kept (``ENDPOINTS``) for the OpenAPI description
(openapi.py), from which the front end's types are generated.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from typing import Annotated, Any

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user
from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic import ValidationError as InputError

from ..services import ServiceError

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

PREFIX = api_bp.url_prefix


def is_api_request():
    """Whether the request in hand is for the API -- for the app's own error handlers."""
    return request.path == PREFIX or request.path.startswith(PREFIX + "/")


# --- Answers --------------------------------------------------------------------


class Model(BaseModel):
    """Base of every request and answer: unknown fields in a request are refused,
    so a misspelt field is an error rather than silently ignored."""

    model_config = ConfigDict(extra="forbid")


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:  # the database keeps UTC without saying so
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


#: A moment in time, sent as UTC with a ``Z``: ``2026-10-06T07:38:11Z``.
UtcDateTime = Annotated[datetime, PlainSerializer(_as_utc, return_type=str, when_used="json")]


class ErrorBody(Model):
    code: str
    message: str
    fields: dict[str, str] | None = None
    details: dict[str, Any] | None = None


class ErrorOut(Model):
    error: ErrorBody


def error(status, code, message, *, fields=None, details=None):
    """An error answer; ``fields`` and ``details`` are always there, ``null`` when empty."""
    body = ErrorOut(error=ErrorBody(code=code, message=message, fields=fields or None, details=details or None))
    return jsonify(body.model_dump(mode="json")), status


def _field_errors(exc):
    """Pydantic's errors as ``{"field.path": "message"}``, the first per field."""
    fields = {}
    for problem in exc.errors(include_url=False):
        name = ".".join(str(part) for part in problem["loc"]) or "body"
        fields.setdefault(name, problem["msg"])
    return fields


def _invalid(exc):
    return error(400, "validation_error", "Some of the input is not valid.", fields=_field_errors(exc))


def _service_error(exc):
    details = dict(exc.details or {})
    fields = details.pop("fields", None)
    return error(exc.http_status, exc.code, exc.message, fields=fields, details=details or None)


# --- Declaring an endpoint --------------------------------------------------------


@dataclass(frozen=True)
class Endpoint:
    method: str
    rule: str
    name: str
    summary: str
    tag: str
    response: type | None
    body: type | None
    query: type | None
    public: bool
    permissions: tuple = field(default_factory=tuple)
    status: int = 200
    #: The files of a multipart request: ``{name: required}``.
    uploads: dict = field(default_factory=dict)
    #: The media type of a file answer, e.g. ``application/pdf``.
    produces: str | None = None


class Api:
    """Endpoints declared on one blueprint. The portal's is ``api`` below; the
    tests make their own to try the plumbing with endpoints of their own."""

    def __init__(self, blueprint):
        self.blueprint = blueprint
        self.endpoints = []
        blueprint.register_error_handler(ServiceError, _service_error)

    def endpoint(self, method, rule, *, response=None, body=None, query=None, public=False, permissions=(),
                 status=200, tag="General", uploads=None, produces=None):
        """Declare an API endpoint; see the module's description."""
        method = method.upper()

        def decorator(view):
            summary = (view.__doc__ or "").strip().split("\n")[0]
            self.endpoints.append(Endpoint(method, rule, view.__name__, summary, tag, response, body, query,
                                           public, tuple(permissions), status, dict(uploads or {}), produces))

            @wraps(view)
            def handle(**path_args):
                if not public and not current_user.is_authenticated:
                    return error(401, "not_signed_in", "Please sign in.")
                if permissions and not all(current_user.can(p) for p in permissions):
                    return error(403, "forbidden", "You do not have permission to do this.")
                arguments = dict(path_args)
                try:
                    if query is not None:
                        arguments["query"] = query.model_validate(request.args.to_dict())
                    if body is not None:
                        data = request.get_json(silent=True)
                        if data is None:
                            return error(400, "validation_error", "The request needs a JSON body.")
                        arguments["body"] = body.model_validate(data)
                except InputError as exc:
                    return _invalid(exc)
                if uploads:
                    files = {name: (request.files.get(name) if request.files.get(name) and
                                    request.files[name].filename else None) for name in uploads}
                    missing = {name: "Choose a file." for name, required in uploads.items()
                               if required and files[name] is None}
                    if missing:
                        return error(400, "validation_error", "A file is missing.", fields=missing)
                    arguments["files"] = files
                return _answer(view(**arguments), status)

            self.blueprint.add_url_rule(rule, endpoint=view.__name__, view_func=handle, methods=[method])
            return handle

        return decorator


api = Api(api_bp)
endpoint = api.endpoint
ENDPOINTS = api.endpoints


def _answer(result, status):
    if result is None:
        return Response(status=204)
    if isinstance(result, Response):
        return result
    if isinstance(result, BaseModel):
        return jsonify(result.model_dump(mode="json")), status
    raise TypeError(f"An endpoint returned {type(result).__name__}; return its response model.")
