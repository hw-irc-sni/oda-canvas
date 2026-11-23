"""Request bodies are validated against the OAS request schema of their operation before the handler runs: a body
that doesn't match gets 400 with the JSON path of the first error. The OAS drives the API.

Only JSON bodies are checked here (multipart uploads, Turtle and SPARQL are checked by their handlers). OpenAPI
3.0 keywords JSON Schema doesn't know (``nullable``, ``discriminator``, ``readOnly`` …) are ignored. ``@type`` is
optional everywhere in a request, references included (the API sets it): it is dropped from ``required`` lists.
"""
import copy
import json
import re
from datetime import datetime
from pathlib import Path

import yaml
from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

from .errors import ApiError

HTTP_METHODS = ("get", "put", "post", "delete", "patch")
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")
FORMATS = FormatChecker()


@FORMATS.checks("date-time")
def _date_time(value) -> bool:
    """RFC 3339 date-time (jsonschema checks it only with an optional package)."""
    if not isinstance(value, str):
        return True
    if not RFC3339.match(value):
        return False
    try:
        datetime.fromisoformat(value.replace("z", "Z").replace("t", "T"))
    except ValueError:
        return False
    return True
JSON_TYPES = ("application/json", "application/merge-patch+json")


def _type_optional(node):
    if isinstance(node, dict):
        out = {k: _type_optional(v) for k, v in node.items()}
        if isinstance(out.get("required"), list) and "@type" in out["required"]:
            out["required"] = [r for r in out["required"] if r != "@type"]
            if not out["required"]:
                del out["required"]
        return out
    if isinstance(node, list):
        return [_type_optional(v) for v in node]
    return node


def _absolute_refs(node, base: str):
    if isinstance(node, dict):
        return {k: (base + v if k == "$ref" and isinstance(v, str) and v.startswith("#") else _absolute_refs(v, base))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_absolute_refs(v, base) for v in node]
    return node


def load_body_validators(oas_path: str) -> dict[str, Draft7Validator]:
    """operationId -> a validator of its JSON request body (operations without one are absent)."""
    oas = _type_optional(yaml.safe_load(Path(oas_path).read_text()))
    base = f"urn:oas:{Path(oas_path).name}"
    registry = Registry().with_resource(base, Resource.from_contents(oas, default_specification=DRAFT7))
    validators = {}
    for item in (oas.get("paths") or {}).values():
        for method in HTTP_METHODS:
            op = item.get(method)
            body = (op or {}).get("requestBody")
            if not body:
                continue
            if "$ref" in body:  # #/components/requestBodies/...
                node = oas
                for part in body["$ref"][2:].split("/"):
                    node = node[part]
                body = node
            content = body.get("content") or {}
            schema = next((content[t]["schema"] for t in JSON_TYPES if t in content and "schema" in content[t]), None)
            if schema is not None:
                validators[op["operationId"]] = Draft7Validator(_absolute_refs(copy.deepcopy(schema), base),
                                                                registry=registry, format_checker=FORMATS)
    return validators


async def check_body(request, validator: Draft7Validator) -> None:
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in JSON_TYPES:
        return
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else None
    except ValueError as exc:
        raise ApiError(400, "400", "Bad Request", f"Invalid JSON: {exc}") from exc
    error = next(iter(sorted(validator.iter_errors(body), key=lambda e: list(e.absolute_path))), None)
    if error is not None:
        where = "/".join(str(p) for p in error.absolute_path) or "(body)"
        raise ApiError(400, "400", "Bad Request", f"The body does not match the API schema at {where}: {error.message}")
