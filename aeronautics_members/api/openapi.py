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
    """Every model with how it is used: what comes in as it is accepted
    ("validation": a field with a default may be left out), what goes out as
    it is sent ("serialization": a time is a string there, every field is set)."""
    found = {(ErrorOut, "serialization")}
    for declared in ENDPOINTS:
        if declared.body is not None:
            found.add((declared.body, "validation"))
        if declared.query is not None:
            found.add((declared.query, "validation"))
        if declared.response is not None:
            found.add((declared.response, "serialization"))
    return sorted(found, key=lambda pair: (pair[0].__name__, pair[1]))


def build():
    """The OpenAPI document, as a dict."""
    keyed, definitions = models_json_schema(_models(), ref_template="#/components/schemas/{model}")
    schemas = definitions.get("$defs", {})

    def ref(model, mode="serialization"):
        return keyed[(model, mode)]

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
                "required": True, "content": {"application/json": {"schema": ref(declared.body, "validation")}}}
        if declared.uploads:
            operation["requestBody"] = {"required": True, "content": {"multipart/form-data": {"schema": {
                "type": "object",
                "properties": {name: {"type": "string", "format": "binary"} for name in declared.uploads},
                "required": [name for name, required in declared.uploads.items() if required],
            }}}}
        if declared.produces is not None:
            operation["responses"][str(declared.status)] = {
                "description": "The file", "content": {declared.produces: {"schema": {"type": "string",
                                                                                         "format": "binary"}}}}
        elif declared.response is not None:
            operation["responses"][str(declared.status)] = {
                "description": "OK", "content": {"application/json": {"schema": ref(declared.response)}}}
        else:
            operation["responses"]["204"] = {"description": "Done."}
        relevant = {"400"} if declared.body or declared.query or declared.uploads else set()
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
