"""/knowledgeTerm (search) and /knowledgeTerm/describe (describe, layer chain) over the ontology modules."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, require_operation
from ..errors import ApiError
from ..ontology.terms import Terms
from .deps import session

router = APIRouter()


def flag(request: Request, name: str) -> bool:
    raw = request.query_params.get(name, "false").lower()
    if raw not in ("true", "false"):
        raise ApiError(400, "400", "Bad Request", f"{name} must be true or false")
    return raw == "true"


@router.get("/knowledgeTerm")
async def search_terms(request: Request, s: AsyncSession = Depends(session),
                       _: Principal = Depends(require_operation("retrieveKnowledgeTerm"))):
    params = request.query_params
    try:
        limit = int(params.get("limit", "25"))
    except ValueError:
        raise ApiError(400, "400", "Bad Request", "limit must be an integer") from None
    return await Terms(s, request.app.state.graphs.store).find(params.get("q"), limit,
                                                               flag(request, "includeUnpublished"))


@router.get("/knowledgeTerm/describe")
async def describe_term(request: Request, s: AsyncSession = Depends(session),
                        _: Principal = Depends(require_operation("describeKnowledgeTerm"))):
    return await Terms(s, request.app.state.graphs.store).lookup(
        request.query_params.get("iri"), flag(request, "layerChain"), flag(request, "includeUnpublished"))
