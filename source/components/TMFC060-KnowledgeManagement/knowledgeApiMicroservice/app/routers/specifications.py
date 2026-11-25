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
from ..ontology.governance import Governance
from .deps import registry_for, session


def governance_for(request: Request, s: AsyncSession) -> Governance:
    return Governance(registry_for(request, s), request.app.state.lifecycles)

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
    payload = await json_body(request)
    registry = registry_for(request, s)
    current = (await registry.get_row(id)).lifecycle_status
    if "lifecycleStatus" in payload and payload["lifecycleStatus"] != current:
        if set(payload) - {"lifecycleStatus", "@type"}:
            raise ApiError(400, "400", "Bad Request", "Change lifecycleStatus on its own, without other fields")
        body = await governance_for(request, s).transition_module(id, payload["lifecycleStatus"], principal)
    else:
        body = await registry.patch(id, payload, principal)
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


def mappings_for(request: Request, s: AsyncSession):
    from ..ontology.mappings import Mappings
    return Mappings(registry_for(request, s))


def present_mapping(request: Request, mappings, m) -> dict:
    return present(request, mappings.present(request.app.state.settings.base_path, m))


@router.get("/knowledgeSpecification/{id}/mapping")
async def list_mappings(id: str, request: Request, s: AsyncSession = Depends(session),
                        _: Principal = Depends(require_operation("listKnowledgeSpecificationMapping"))):
    from sqlalchemy import select
    from ..db.models import Mapping
    mappings = mappings_for(request, s)
    await mappings.bridge_module(id)
    rows = (await s.scalars(select(Mapping).where(Mapping.specification_id == id).order_by(Mapping.id))).all()
    return list_response(request, [mappings.present(request.app.state.settings.base_path, m) for m in rows])


@router.post("/knowledgeSpecification/{id}/mapping", status_code=201)
async def create_mapping(id: str, request: Request, s: AsyncSession = Depends(session),
                         _: Principal = Depends(require_operation("createKnowledgeSpecificationMapping"))):
    mappings = mappings_for(request, s)
    m = await mappings.create(id, await json_body(request))
    return JSONResponse(present_mapping(request, mappings, m), status_code=201)


@router.get("/knowledgeSpecification/{id}/mapping/{mappingId}")
async def retrieve_mapping(id: str, mappingId: str, request: Request, s: AsyncSession = Depends(session),
                           _: Principal = Depends(require_operation("retrieveKnowledgeSpecificationMapping"))):
    mappings = mappings_for(request, s)
    await mappings.bridge_module(id)
    return present_mapping(request, mappings, await mappings.get(id, mappingId))


@router.patch("/knowledgeSpecification/{id}/mapping/{mappingId}")
async def patch_mapping(id: str, mappingId: str, request: Request, s: AsyncSession = Depends(session),
                        principal: Principal = Depends(require_operation("patchKnowledgeSpecificationMapping"))):
    payload = await json_body(request)
    mappings = mappings_for(request, s)
    current = (await mappings.get(id, mappingId)).lifecycle_status
    if "lifecycleStatus" in payload and payload["lifecycleStatus"] != current:
        if set(payload) - {"lifecycleStatus"}:
            raise ApiError(400, "400", "Bad Request", "Change lifecycleStatus on its own, without other fields")
        m = await governance_for(request, s).transition_mapping(id, mappingId, payload["lifecycleStatus"], principal)
    else:
        m = await mappings.patch(id, mappingId, payload, principal)
    return present_mapping(request, mappings, m)


@router.delete("/knowledgeSpecification/{id}/mapping/{mappingId}", status_code=204)
async def delete_mapping(id: str, mappingId: str, request: Request, s: AsyncSession = Depends(session),
                         principal: Principal = Depends(require_operation("deleteKnowledgeSpecificationMapping"))):
    await mappings_for(request, s).delete(id, mappingId, principal)
    return Response(status_code=204)


@router.get("/knowledgeSpecification/{id}/review")
async def list_reviews(id: str, request: Request, s: AsyncSession = Depends(session),
                       _: Principal = Depends(require_operation("listKnowledgeSpecificationReview"))):
    return list_response(request, await governance_for(request, s).list_reviews(id))


@router.post("/knowledgeSpecification/{id}/review", status_code=201)
async def create_review(id: str, request: Request, s: AsyncSession = Depends(session),
                        principal: Principal = Depends(require_operation("createKnowledgeSpecificationReview"))):
    item = await governance_for(request, s).create_review(id, await json_body(request), principal)
    return JSONResponse(item, status_code=201)


@router.get("/knowledgeSpecification/{id}/transition")
async def list_transitions(id: str, request: Request, s: AsyncSession = Depends(session),
                           _: Principal = Depends(require_operation("listKnowledgeSpecificationTransition"))):
    return list_response(request, await governance_for(request, s).list_transitions(id))


@router.get("/knowledgeSpecification/{id}/validation")
async def retrieve_validation(id: str, request: Request, s: AsyncSession = Depends(session),
                              _: Principal = Depends(require_operation("retrieveKnowledgeSpecificationValidation"))):
    return await registry_for(request, s).last_report(id)


@router.post("/knowledgeSpecification/{id}/validation")
async def run_validation(id: str, request: Request, s: AsyncSession = Depends(session),
                         _: Principal = Depends(require_operation("runKnowledgeSpecificationValidation"))):
    return await registry_for(request, s).validate(id)


@router.post("/knowledgeSpecification/{id}/validation/document")
async def validate_document(id: str, request: Request, s: AsyncSession = Depends(session),
                            _: Principal = Depends(require_operation("validateDocument"))):
    return await registry_for(request, s).validate_document(id, await request.body(),
                                                           request.headers.get("content-type", "text/turtle"))


@router.get("/schema/{name}")
async def retrieve_schema(name: str, _: Principal = Depends(require_operation("retrieveSchema"))):
    path = SCHEMAS_DIR / name
    if path.parent != SCHEMAS_DIR or not path.is_file():
        raise ApiError(404, "404", "Not Found", f"No schema '{name}'")
    return Response(path.read_bytes(), media_type="application/schema+json")
