"""/knowledge, /knowledge/{id}, its /graph (Graph Store Protocol) and /query (read-only SPARQL)."""
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, require_operation
from ..dataset import ModuleInfo, query_dataset
from ..db.models import KnowledgeSpecification as SpecRow
from ..errors import ApiError
from ..graphstore import kb_graph
from ..listing import list_response, present
from ..query import effective_timeout, run_query
from ..routers.deps import session
from ..routers.specifications import json_body
from .service import KnowledgeBases
from .vectors import Vectors

router = APIRouter()
GRAPH_TYPES = ("text/turtle", "application/ld+json")


def kbs_for(request: Request, s: AsyncSession) -> KnowledgeBases:
    state = request.app.state
    return KnowledgeBases(s, state.graphs, state.settings, state.events,
                          Vectors(state.qdrant, state.settings.embedding_dimension), state.lifecycles)


def graph_media_type(request: Request) -> str:
    accept = request.headers.get("accept", "")
    return "application/ld+json" if "application/ld+json" in accept and "text/turtle" not in accept \
        else "text/turtle"


def body_media_type(request: Request) -> str:
    media_type = request.headers.get("content-type", "text/turtle").split(";")[0].strip().lower()
    if media_type not in GRAPH_TYPES:
        raise ApiError(415, "415", "Unsupported Media Type", f"Use one of {', '.join(GRAPH_TYPES)}")
    return media_type


@router.get("/knowledge")
async def list_knowledge(request: Request, s: AsyncSession = Depends(session),
                         _: Principal = Depends(require_operation("listKnowledge"))):
    return list_response(request, await kbs_for(request, s).all_bodies())


@router.post("/knowledge", status_code=201)
async def create_knowledge(request: Request, s: AsyncSession = Depends(session),
                           principal: Principal = Depends(require_operation("createKnowledge"))):
    body = await kbs_for(request, s).create(await json_body(request), principal)
    return JSONResponse(present(request, body), status_code=201)


@router.get("/knowledge/{id}")
async def retrieve_knowledge(id: str, request: Request, s: AsyncSession = Depends(session),
                             _: Principal = Depends(require_operation("retrieveKnowledge"))):
    kbs = kbs_for(request, s)
    return present(request, await kbs.body(await kbs.get_row(id)))


@router.patch("/knowledge/{id}")
async def patch_knowledge(id: str, request: Request, s: AsyncSession = Depends(session),
                          principal: Principal = Depends(require_operation("patchKnowledge"))):
    return present(request, await kbs_for(request, s).patch(id, await json_body(request), principal))


@router.delete("/knowledge/{id}", status_code=204)
async def delete_knowledge(id: str, request: Request, s: AsyncSession = Depends(session),
                           principal: Principal = Depends(require_operation("deleteKnowledge"))):
    await kbs_for(request, s).delete(id, principal)
    return Response(status_code=204)


# ---- graph (Graph Store Protocol) ------------------------------------------------------------------------------
@router.get("/knowledge/{id}/graph")
async def retrieve_graph(id: str, request: Request, s: AsyncSession = Depends(session),
                         _: Principal = Depends(require_operation("retrieveKnowledgeGraph"))):
    kbs = kbs_for(request, s)
    await kbs.get_row(id)
    media_type = graph_media_type(request)
    return Response(kbs.graphs.export(kb_graph(id), media_type), media_type=media_type)


@router.put("/knowledge/{id}/graph", status_code=204)
async def replace_graph(id: str, request: Request, s: AsyncSession = Depends(session),
                        principal: Principal = Depends(require_operation("replaceKnowledgeGraph"))):
    await kbs_for(request, s).write_graph(id, principal, "replace", await request.body(), body_media_type(request))
    return Response(status_code=204)


@router.post("/knowledge/{id}/graph", status_code=204)
async def merge_graph(id: str, request: Request, s: AsyncSession = Depends(session),
                      principal: Principal = Depends(require_operation("mergeKnowledgeGraph"))):
    await kbs_for(request, s).write_graph(id, principal, "merge", await request.body(), body_media_type(request))
    return Response(status_code=204)


@router.delete("/knowledge/{id}/graph", status_code=204)
async def clear_graph(id: str, request: Request, s: AsyncSession = Depends(session),
                      principal: Principal = Depends(require_operation("clearKnowledgeGraph"))):
    await kbs_for(request, s).write_graph(id, principal, "clear")
    return Response(status_code=204)


# ---- query -----------------------------------------------------------------------------------------------------
async def query_text(request: Request) -> str:
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    raw = await request.body()
    if media_type == "application/sparql-query":
        text = raw.decode("utf-8", errors="replace")
    elif media_type == "application/x-www-form-urlencoded":
        text = (parse_qs(raw.decode("utf-8", errors="replace")).get("query") or [""])[0]
    else:
        raise ApiError(415, "415", "Unsupported Media Type",
                       "Send the query as application/sparql-query or a form field 'query'")
    if not text.strip():
        raise ApiError(400, "400", "Bad Request", "The query is empty")
    return text


@router.post("/knowledge/{id}/query")
async def query_knowledge(id: str, request: Request, s: AsyncSession = Depends(session),
                          _: Principal = Depends(require_operation("queryKnowledge"))):
    settings = request.app.state.settings
    kbs = kbs_for(request, s)
    row = await kbs.get_row(id)
    text = await query_text(request)
    try:
        requested = int(request.query_params["timeout"]) if "timeout" in request.query_params else None
    except ValueError:
        raise ApiError(400, "400", "Bad Request", "timeout must be an integer") from None
    if requested is not None and requested < 1:
        raise ApiError(400, "400", "Bad Request", "timeout must be at least 1")
    modules = {m.id: ModuleInfo(m.lifecycle_status, tuple(r["id"] for r in m.body.get("imports") or []))
               for m in (await s.scalars(select(SpecRow))).all()}
    dataset = query_dataset(id, [r["id"] for r in row.body.get("conformsTo") or []], modules)
    graph_format = graph_media_type(request)
    result = await run_query(settings.store_path, text, dataset,
                             timeout=effective_timeout(requested, settings.query_timeout_default,
                                                       settings.query_timeout_max),
                             row_cap=settings.query_row_cap, allowed_services=frozenset(),
                             graph_format=graph_format)
    headers = {"X-Result-Truncated": "true"} if result.truncated else {}
    if result.kind == "graph":
        return Response(result.body, media_type=graph_format, headers=headers)
    return JSONResponse(result.body, media_type="application/sparql-results+json", headers=headers)
