"""/hub: event subscriptions (TMF630). A subscription can be read and deleted only by its creator."""
import secrets

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, require_operation
from ..db.models import HubSubscription
from ..errors import ApiError
from ..events import CallbackRefused, check_callback, parse_query
from .deps import session
from .specifications import json_body

router = APIRouter()


def owner_of(principal: Principal) -> str:
    return f"client:{principal.client}" if "canvasSystemRole" in principal.roles and principal.client \
        else f"user:{principal.subject}"


def present(sub: HubSubscription) -> dict:
    out = {"@type": "Hub", "id": sub.id, "callback": sub.callback}
    if sub.query:
        out["query"] = sub.query
    return out


async def own(s: AsyncSession, hub_id: str, principal: Principal) -> HubSubscription:
    sub = await s.get(HubSubscription, hub_id)
    if sub is None or sub.owner != owner_of(principal):
        raise ApiError(404, "404", "Not Found", f"No hub '{hub_id}'")
    return sub


@router.post("/eventSource/{kind}/{token}", status_code=204)
@router.post("/eventSource/{kind}/{token}/listener/{event_name}", status_code=204)
async def receive_event(kind: str, token: str, request: Request, s: AsyncSession = Depends(session),
                        event_name: str | None = None):
    """A provider's notification (#46): guarded by the secret token of its registration, not by roles."""
    from ..event_sources import receive
    await receive(s, request.app.state.settings, kind, token, await json_body(request), request.app.state.batcher)
    return Response(status_code=204)


@router.post("/hub", status_code=201)
async def create_hub(request: Request, s: AsyncSession = Depends(session),
                     principal: Principal = Depends(require_operation("createHub"))):
    payload = await json_body(request)
    callback = payload.get("callback")
    if not isinstance(callback, str) or not callback:
        raise ApiError(400, "400", "Bad Request", "callback is required")
    query = payload.get("query") or None
    if query is not None and not isinstance(query, str):
        raise ApiError(400, "400", "Bad Request", "query must be a string")
    parse_query(query)
    try:
        await check_callback(callback, request.app.state.settings.event_callback_allowed_hosts)
    except CallbackRefused as exc:
        raise ApiError(400, "400", "Bad Request", f"Callback refused: {exc}") from exc
    sub = HubSubscription(id=f"hub-{secrets.token_hex(8)}", callback=callback, query=query, owner=owner_of(principal))
    s.add(sub)
    await s.commit()
    return JSONResponse(present(sub), status_code=201)


@router.get("/hub/{id}")
async def retrieve_hub(id: str, s: AsyncSession = Depends(session),
                       principal: Principal = Depends(require_operation("retrieveHub"))):
    return present(await own(s, id, principal))


@router.delete("/hub/{id}", status_code=204)
async def delete_hub(id: str, s: AsyncSession = Depends(session),
                     principal: Principal = Depends(require_operation("deleteHub"))):
    await s.delete(await own(s, id, principal))  # pending deliveries cascade
    await s.commit()
    return Response(status_code=204)
