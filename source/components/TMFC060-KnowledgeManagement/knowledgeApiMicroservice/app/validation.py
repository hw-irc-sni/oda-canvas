"""Request bodies are validated against the OAS request schema of their operation before the handler runs: a body
that doesn't match gets 400 with the JSON path of the first error. The OAS drives the API.

Only JSON bodies are checked here (multipart uploads, Turtle and SPARQL are checked by their handlers). OpenAPI
3.0 keywords JSON Schema doesn't know (``nullable``, ``readOnly`` …) are ignored, except ``discriminator``: a
``oneOf`` with a discriminator mapping picks its variant by ``@type`` (TMF variants such as ``PartyRef`` and
``PartyRoleRef`` would otherwise both match, which a plain ``oneOf`` rejects). ``@type`` is optional everywhere in a
request, references included: it is dropped from ``required`` lists, and the API sets it on nested objects from the
schema (the single discriminator mapping or ``@type`` enum value; between ``oneOf`` variants, the one whose own
properties the object uses most) before the handler reads the body. Top-level resources get theirs from their
service. No string in a JSON body may hold a NUL character (PostgreSQL can't store one).
"""
import copy
import json
import re
from dataclasses import dataclass
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


URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s]*$")


@FORMATS.checks("uri")
def _uri(value) -> bool:
    """An absolute URI, scheme first (RFC 3986; jsonschema checks it only with an optional package)."""
    return not isinstance(value, str) or bool(URI.match(value))


JSON_TYPES = ("application/json", "application/merge-patch+json")


@dataclass(frozen=True)
class BodyRule:
    """The JSON request body of an operation: its validator, and its schema for the ``@type`` defaults."""
    validator: Draft7Validator
    schema: dict
    oas: dict

    def is_valid(self, body) -> bool:
        return self.validator.is_valid(body)


def _resolve(schema, oas: dict) -> dict:
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str) and schema["$ref"].startswith("#/"):
        node = oas
        for part in schema["$ref"][2:].split("/"):
            node = node[part]
        schema = node
    return schema if isinstance(schema, dict) else {}


def _flatten(schema, oas: dict) -> tuple[dict, dict, list]:
    """(properties, @type discriminator mapping, oneOf/anyOf variants) of a schema and its allOf parts."""
    schema = _resolve(schema, oas)
    properties, mapping, variants = dict(schema.get("properties") or {}), {}, []
    disc = schema.get("discriminator") or {}
    if disc.get("propertyName", "@type") == "@type":
        mapping = dict(disc.get("mapping") or {})
    for key in ("oneOf", "anyOf"):
        if mapping and key in schema:
            variants = [v for v in schema[key] if "$ref" in v]
    for part in schema.get("allOf") or []:
        p, m, v = _flatten(part, oas)
        properties = {**p, **properties}
        mapping = mapping or m
        variants = variants or v
    return properties, mapping, variants


def with_types(value, schema, oas: dict, nested: bool = False):
    """``value`` with ``@type`` set on the nested objects that lack it, as the schema names them."""
    if isinstance(value, list):
        items = _resolve(schema, oas).get("items")
        return [with_types(v, items, oas, True) for v in value] if items else value
    if not isinstance(value, dict):
        return value
    properties, mapping, variants = _flatten(schema, oas)
    if variants and mapping:  # pick the variant named by @type, else the one whose properties fit best
        by_ref = {ref: name for name, ref in mapping.items()}
        named = [v for v in variants if by_ref.get(v["$ref"]) == value.get("@type")]
        if named:
            chosen = named[0]
        else:
            def fit(v):
                own = _flatten(v, oas)[0]
                return len(set(own) & set(value))
            chosen = max(variants, key=fit)  # max keeps the first of equals
        if nested and "@type" not in value and chosen["$ref"] in by_ref:
            value = {"@type": by_ref[chosen["$ref"]], **value}
        return with_types(value, chosen, oas, nested)
    if nested and "@type" not in value:
        enum = (_resolve(properties.get("@type"), oas) or {}).get("enum") or []
        if len(mapping) == 1:
            value = {"@type": next(iter(mapping)), **value}
        elif len(enum) == 1:
            value = {"@type": enum[0], **value}
    return {k: with_types(v, properties[k], oas, True) if k in properties else v for k, v in value.items()}


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


def _discriminated(node):
    """``oneOf`` + ``discriminator.mapping`` -> ``anyOf`` the variants, and the variant named by ``@type`` must match."""
    if isinstance(node, list):
        return [_discriminated(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: _discriminated(v) for k, v in node.items()}
    mapping = (out.get("discriminator") or {}).get("mapping")
    if "oneOf" in out and mapping:
        prop = out["discriminator"].get("propertyName", "@type")
        out["anyOf"] = out.pop("oneOf")
        out["allOf"] = list(out.get("allOf", [])) + [
            {"properties": {prop: {"enum": list(mapping)}}}] + [
            {"if": {"required": [prop], "properties": {prop: {"const": name}}}, "then": {"$ref": ref}}
            for name, ref in mapping.items()]
    return out


def _has_nul(node) -> bool:
    if isinstance(node, str):
        return "\x00" in node
    if isinstance(node, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in node.items())
    if isinstance(node, list):
        return any(_has_nul(v) for v in node)
    return False


def _absolute_refs(node, base: str):
    if isinstance(node, dict):
        return {k: (base + v if k == "$ref" and isinstance(v, str) and v.startswith("#") else _absolute_refs(v, base))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_absolute_refs(v, base) for v in node]
    return node


def load_body_validators(oas_path: str) -> dict[str, BodyRule]:
    """operationId -> the rule of its JSON request body (operations without one are absent)."""
    oas = _discriminated(_type_optional(yaml.safe_load(Path(oas_path).read_text())))
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
                validator = Draft7Validator(_absolute_refs(copy.deepcopy(schema), base), registry=registry,
                                            format_checker=FORMATS)
                validators[op["operationId"]] = BodyRule(validator, schema, oas)
    return validators


async def check_body(request, rule: BodyRule) -> None:
    """400 for a body that doesn't match; otherwise the handler's ``request.json()`` gets it with the defaults."""
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in JSON_TYPES:
        return
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else None
    except ValueError as exc:
        raise ApiError(400, "400", "Bad Request", f"Invalid JSON: {exc}") from exc
    if _has_nul(body):
        raise ApiError(400, "400", "Bad Request", "The body holds a NUL character, which isn't allowed in strings")
    error = next(iter(sorted(rule.validator.iter_errors(body), key=lambda e: list(e.absolute_path))), None)
    if error is not None:
        where = "/".join(str(p) for p in error.absolute_path) or "(body)"
        raise ApiError(400, "400", "Bad Request", f"The body does not match the API schema at {where}: {error.message}")
    request._json = with_types(body, rule.schema, rule.oas)  # Starlette's cache of request.json()
