"""TMF630 list handling: fields, offset, limit, sort and equality filters on top-level attributes."""
from fastapi import Request
from fastapi.responses import JSONResponse

from .errors import ApiError

RESERVED = {"fields", "offset", "limit", "sort", "filter"}
ALWAYS = {"id", "href", "@type", "@baseType", "@schemaLocation"}


def absolutize(value, base_url: str):
    """Make every stored path-only href and @schemaLocation absolute with the request base URL."""
    if isinstance(value, dict):
        return {k: (base_url + v if k in ("href", "@schemaLocation") and isinstance(v, str) and v.startswith("/")
                    else absolutize(v, base_url)) for k, v in value.items()}
    if isinstance(value, list):
        return [absolutize(v, base_url) for v in value]
    return value


def present(request: Request, body: dict) -> dict:
    """A stored body as served: absolute hrefs, then the requested fields."""
    return select_fields(absolutize(body, str(request.base_url).rstrip("/")), request.query_params.get("fields"))


def select_fields(body: dict, fields: str | None) -> dict:
    if not fields:
        return body
    wanted = {f.strip() for f in fields.split(",") if f.strip()} | ALWAYS
    return {k: v for k, v in body.items() if k in wanted}


def _matches(body: dict, filters: dict[str, str]) -> bool:
    for key, expected in filters.items():
        value = body.get(key)
        if isinstance(value, bool):
            value = str(value).lower()
        if value is None or str(value) != expected:
            return False
    return True


def _sort_key(field):
    def key(body):
        value = body.get(field)
        return (value is None, str(value) if value is not None else "")
    return key


def list_response(request: Request, bodies: list[dict]) -> JSONResponse:
    params = request.query_params
    if "filter" in params:  # no JSONPath (#26, #49): equality filters attribute=value only
        raise ApiError(400, "400", "Bad Request", "The JSONPath filter is not supported; use attribute=value")
    try:
        offset = int(params.get("offset", 0))
        limit = int(params["limit"]) if "limit" in params else None
    except ValueError as exc:
        raise ApiError(400, "400", "Bad Request", "offset and limit must be integers") from exc
    if offset < 0 or (limit is not None and limit < 1):
        raise ApiError(400, "400", "Bad Request", "offset must be >= 0 and limit >= 1")
    filters = {k: v for k, v in params.items() if k not in RESERVED}
    items = [b for b in bodies if _matches(b, filters)]
    for field in reversed([f.strip() for f in params.get("sort", "").split(",") if f.strip()]):
        items.sort(key=_sort_key(field.lstrip("-")), reverse=field.startswith("-"))
    total = len(items)
    page = items[offset: offset + limit if limit is not None else None]
    page = [present(request, b) for b in page]
    return JSONResponse(page, headers={"X-Total-Count": str(total), "X-Result-Count": str(len(page))})
