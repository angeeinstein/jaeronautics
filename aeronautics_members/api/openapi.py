"""The API described as OpenAPI 3.1, from the endpoints' own declarations.

Nothing here is written by hand: every path, parameter, request and answer
comes from ``@endpoint(...)`` and its Pydantic models, so the description
cannot drift from the code. ``flask api-schema`` writes it; the front end
generates its TypeScript types from it.
"""

import re

from pydantic.json_schema import models_json_schema

from ._core import ENDPOINTS, ErrorOut, PREFIX

_PARAMETER = re.compile(r"<(?:(\w+):)?(\w+)>")
_TYPES = {"int": {"type": "integer"}, "float": {"type": "number"}, "path": {"type": "string"},
          "string": {"type": "string"}, None: {"type": "string"}}

_ERRORS = {
    "400": "The input is not valid (see error.fields).",
    "401": "Not signed in.",
    "403": "Signed in, without the permission.",
    "404": "Not found.",
    "409": "Not possible in the current state.",
}


def _models():
    found = {ErrorOut}
    for declared in ENDPOINTS:
        found.update(model for model in (declared.response, declared.body, declared.query) if model is not None)
    return sorted(found, key=lambda model: model.__name__)


def build():
    """The OpenAPI document, as a dict."""
    models = _models()
    # One schema per model, as it is sent: a time is a string there.
    keyed, definitions = models_json_schema(
        [(model, "serialization") for model in models], ref_template="#/components/schemas/{model}")
    schemas = definitions.get("$defs", {})

    def ref(model):
        return keyed[(model, "serialization")]

    paths = {}
    for declared in sorted(ENDPOINTS, key=lambda e: (e.rule, e.method)):
        path = PREFIX + _PARAMETER.sub(lambda m: "{" + m.group(2) + "}", declared.rule)
        operation = {
            "operationId": declared.name,
            "summary": declared.summary,
            "tags": [declared.tag],
            "parameters": [
                {"name": name, "in": "path", "required": True, "schema": _TYPES.get(kind, _TYPES[None])}
                for kind, name in _PARAMETER.findall(declared.rule)
            ],
            "responses": {},
        }
        if declared.query is not None:
            query_schema = declared.query.model_json_schema()
            required = set(query_schema.get("required", []))
            for name, schema in query_schema.get("properties", {}).items():
                operation["parameters"].append(
                    {"name": name, "in": "query", "required": name in required, "schema": schema})
        if declared.body is not None:
            operation["requestBody"] = {
                "required": True, "content": {"application/json": {"schema": ref(declared.body)}}}
        if declared.response is not None:
            operation["responses"][str(declared.status)] = {
                "description": "OK", "content": {"application/json": {"schema": ref(declared.response)}}}
        else:
            operation["responses"]["204"] = {"description": "Done."}
        relevant = {"400"} if declared.body or declared.query else set()
        if not declared.public:
            relevant.add("401")
        if declared.permissions:
            relevant.add("403")
        if _PARAMETER.search(declared.rule):
            relevant.add("404")
        if declared.method != "GET":
            relevant.add("409")
        for status in sorted(relevant):
            operation["responses"][status] = {
                "description": _ERRORS[status], "content": {"application/json": {"schema": ref(ErrorOut)}}}
        if not operation["parameters"]:
            del operation["parameters"]
        if declared.permissions:
            operation["x-permissions"] = list(declared.permissions)
        paths.setdefault(path, {})[declared.method.lower()] = operation

    return {
        "openapi": "3.1.0",
        "info": {"title": "Joanneum Aeronautics member portal", "version": "1"},
        "paths": paths,
        "components": {"schemas": schemas},
    }
