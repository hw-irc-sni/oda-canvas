"""/knowledgeSpecification, /knowledgeSpecification/{id}, its /content, and /schema/{name}."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, require_operation
from ..errors import ApiError
from ..graphstore import module_graph
from ..listing import list_response, present
from .deps import registry_for, session

router = APIRouter()
SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "api" / "schemas"


async def json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise ApiError(400, "400", "Bad Request", f"Invalid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "400", "Bad Request", "The body must be a JSON object")
    return body


@router.get("/knowledgeSpecification")
async def list_specifications(request: Request, s: AsyncSession = Depends(session),
                              _: Principal = Depends(require_operation("listKnowledgeSpecification"))):
    return list_response(request, await registry_for(request, s).all_bodies())


@router.post("/knowledgeSpecification", status_code=201)
async def create_specification(request: Request, s: AsyncSession = Depends(session),
                               _: Principal = Depends(require_operation("createKnowledgeSpecification"))):
    body = await registry_for(request, s).create(await json_body(request))
    return JSONResponse(present(request, body), status_code=201)


@router.get("/knowledgeSpecification/{id}")
async def retrieve_specification(id: str, request: Request, s: AsyncSession = Depends(session),
                                 _: Principal = Depends(require_operation("retrieveKnowledgeSpecification"))):
    row = await registry_for(request, s).get_row(id)
    return present(request, row.body)


@router.patch("/knowledgeSpecification/{id}")
async def patch_specification(id: str, request: Request, s: AsyncSession = Depends(session),
                              principal: Principal = Depends(require_operation("patchKnowledgeSpecification"))):
    body = await registry_for(request, s).patch(id, await json_body(request), principal)
    return present(request, body)


@router.delete("/knowledgeSpecification/{id}", status_code=204)
async def delete_specification(id: str, request: Request, s: AsyncSession = Depends(session),
                               principal: Principal = Depends(require_operation("deleteKnowledgeSpecification"))):
    await registry_for(request, s).delete(id, principal)
    return Response(status_code=204)


@router.get("/knowledgeSpecification/{id}/content")
async def retrieve_content(id: str, request: Request, s: AsyncSession = Depends(session),
                           _: Principal = Depends(require_operation("retrieveKnowledgeSpecificationContent"))):
    registry = registry_for(request, s)
    await registry.get_row(id)
    return Response(registry.graphs.export(module_graph(id), "text/turtle"), media_type="text/turtle")


@router.put("/knowledgeSpecification/{id}/content", status_code=204)
async def replace_content(id: str, request: Request, s: AsyncSession = Depends(session),
                          principal: Principal = Depends(require_operation("replaceKnowledgeSpecificationContent"))):
    media_type = request.headers.get("content-type", "text/turtle")
    if not media_type.startswith("text/turtle"):
        raise ApiError(415, "415", "Unsupported Media Type", "Module content is text/turtle")
    await registry_for(request, s).put_content(id, await request.body(), "text/turtle", principal)
    return Response(status_code=204)


@router.get("/knowledgeSpecification/{id}/validation")
async def retrieve_validation(id: str, request: Request, s: AsyncSession = Depends(session),
                              _: Principal = Depends(require_operation("retrieveKnowledgeSpecificationValidation"))):
    return await registry_for(request, s).last_report(id)


@router.post("/knowledgeSpecification/{id}/validation")
async def run_validation(id: str, request: Request, s: AsyncSession = Depends(session),
                         _: Principal = Depends(require_operation("runKnowledgeSpecificationValidation"))):
    return await registry_for(request, s).validate(id)


@router.get("/schema/{name}")
async def retrieve_schema(name: str, _: Principal = Depends(require_operation("retrieveSchema"))):
    path = SCHEMAS_DIR / name
    if path.parent != SCHEMAS_DIR or not path.is_file():
        raise ApiError(404, "404", "Not Found", f"No schema '{name}'")
    return Response(path.read_bytes(), media_type="application/schema+json")
