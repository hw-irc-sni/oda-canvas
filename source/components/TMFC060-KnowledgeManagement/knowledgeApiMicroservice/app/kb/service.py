"""Knowledge-base resources: metadata in Postgres, the instance graph in the store, vectors in Qdrant.

A knowledge-base can be changed (fields, graph) in every lifecycle state except deprecated. A caller with
canvasSystemRole (another component) is recorded as the owner of the knowledge-bases it creates and may change only
those; a knowledge-editor may change any. lifecycleStatus follows the Knowledge x-lifecycle of the OAS.
"""
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal
from ..config import Settings
from ..db.models import Document, StateTransition
from ..db.models import Knowledge as KnowledgeRow
from ..db.models import KnowledgeSpecification as SpecRow
from ..errors import ApiError
from ..events import Event
from ..graphstore import GraphStore, kb_graph
from .vectors import Vectors

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
STORE_KINDS = {"graph", "vector", "hybrid"}
CATEGORIES = {"know-what", "know-why", "know-how", "know-who"}
SOURCE_KINDS = {"upload", "tmf639", "tmf638", "tmf642", "tmf628", "tmf921", "remote"}
READ_ONLY = {"id", "href", "lastUpdate", "statistics", "@baseType", "@schemaLocation"}
EDITOR = "knowledge-editor"
SYSTEM = "canvasSystemRole"


def bad_request(message: str) -> ApiError:
    return ApiError(400, "400", "Bad Request", message)


def conflict(message: str) -> ApiError:
    return ApiError(409, "409", "Conflict", message)


def forbidden(message: str) -> ApiError:
    return ApiError(403, "403", "Forbidden", message)


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:56] or "kb"


def owner_party(client: str) -> dict:
    return {"role": "owner", "@type": "RelatedPartyRefOrPartyRoleRef",
            "partyOrPartyRole": {"id": client, "name": client, "@type": "PartyRef"}}


def owners(body: dict) -> list[dict]:
    return [p for p in body.get("relatedParty") or [] if p.get("role") == "owner"]


def validate_fields(payload: dict, *, creating: bool) -> None:
    if creating and not (isinstance(payload.get("name"), str) and payload["name"].strip()):
        raise bad_request("name is required")
    if "storeKind" in payload and payload["storeKind"] not in STORE_KINDS:
        raise bad_request(f"storeKind must be one of {', '.join(sorted(STORE_KINDS))}")
    for category in payload.get("knowledgeCategory") or []:
        if category not in CATEGORIES:
            raise bad_request(f"knowledgeCategory must be among {', '.join(sorted(CATEGORIES))}")
    for source in payload.get("source") or []:
        if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
            raise bad_request(f"source kind must be one of {', '.join(sorted(SOURCE_KINDS))}")
    retrieval = payload.get("retrieval") or {}
    if "topK" in retrieval and not (isinstance(retrieval["topK"], int) and retrieval["topK"] >= 1):
        raise bad_request("retrieval.topK must be an integer >= 1")
    if "scoreThreshold" in retrieval and not (isinstance(retrieval["scoreThreshold"], (int, float))
                                              and 0 <= retrieval["scoreThreshold"] <= 1):
        raise bad_request("retrieval.scoreThreshold must be between 0 and 1")


class KnowledgeBases:
    def __init__(self, session: AsyncSession, graphs: GraphStore, settings: Settings, events, vectors: Vectors,
                 lifecycles):
        self.session = session
        self.graphs = graphs
        self.settings = settings
        self.events = events
        self.vectors = vectors
        self.lifecycles = lifecycles

    def href(self, kb_id: str) -> str:
        return f"{self.settings.base_path}/knowledge/{kb_id}"

    async def publish(self, kind: str, body: dict) -> None:
        await self.events.publish(Event(f"Knowledge{kind}Event", "knowledge", body))

    async def get_row(self, kb_id: str) -> KnowledgeRow:
        row = await self.session.get(KnowledgeRow, kb_id)
        if row is None:
            raise ApiError(404, "404", "Not Found", f"No knowledge '{kb_id}'")
        return row

    # ---- presentation --------------------------------------------------------------------------------------
    async def statistics(self, row: KnowledgeRow) -> dict:
        documents = await self.session.scalar(select(func.count()).select_from(Document)
                                              .where(Document.knowledge_id == row.id))
        stats = {"triples": self.graphs.count_union(self.graphs.kb_graphs(row.id)), "documents": documents}
        if row.store_kind in ("vector", "hybrid"):
            chunks = await self.vectors.count(row.id)
            if chunks is not None:
                stats["chunks"] = chunks
        return stats

    async def body(self, row: KnowledgeRow) -> dict:
        return {**row.body, "statistics": await self.statistics(row)}

    async def all_bodies(self) -> list[dict]:
        rows = (await self.session.scalars(select(KnowledgeRow).order_by(KnowledgeRow.id))).all()
        return [await self.body(r) for r in rows]

    # ---- helpers -------------------------------------------------------------------------------------------
    async def resolve_conforms_to(self, refs) -> list[dict]:
        if not isinstance(refs, list):
            raise bad_request("conformsTo must be a list of KnowledgeSpecificationRef")
        resolved = []
        for ref in refs:
            row = None
            if isinstance(ref, dict) and ref.get("id"):
                row = await self.session.get(SpecRow, ref["id"])
            if row is None and isinstance(ref, dict) and ref.get("iri"):
                row = await self.session.scalar(select(SpecRow).where(SpecRow.iri == ref["iri"]))
            if row is None:
                raise bad_request(f"conformsTo names no registered module: {ref}")
            if all(r["id"] != row.id for r in resolved):
                resolved.append({"@type": "KnowledgeSpecificationRef", "id": row.id, "iri": row.iri, "name": row.name,
                                 "href": f"{self.settings.base_path}/knowledgeSpecification/{row.id}"})
        return resolved

    async def new_id(self, payload: dict) -> str:
        if payload.get("id"):
            if not isinstance(payload["id"], str) or not ID_PATTERN.match(payload["id"]):
                raise bad_request("id must match ^[a-z0-9][a-z0-9-]{0,62}$")
            if await self.session.get(KnowledgeRow, payload["id"]):
                raise conflict(f"knowledge '{payload['id']}' already exists")
            return payload["id"]
        base = candidate = slugify(payload["name"])
        while await self.session.get(KnowledgeRow, candidate):
            candidate = f"{base}-{secrets.token_hex(2)}"
        return candidate

    def check_writer(self, row: KnowledgeRow, principal: Principal) -> None:
        """A knowledge-editor may change any KB; canvasSystemRole only the KBs its client owns."""
        if EDITOR in principal.roles:
            return
        if SYSTEM in principal.roles:
            if any(p["partyOrPartyRole"].get("id") == principal.client for p in owners(row.body)):
                return
            raise forbidden("A component may change only the knowledge-bases it created")
        raise forbidden(f"Changing a knowledge-base needs the role {EDITOR}")

    def check_writable(self, row: KnowledgeRow) -> None:
        if row.lifecycle_status == "deprecated":
            raise conflict("A deprecated knowledge-base cannot be changed")

    def save(self, row: KnowledgeRow | None, body: dict) -> KnowledgeRow:
        body["lastUpdate"] = now()
        values = dict(name=body["name"], lifecycle_status=body["lifecycleStatus"], store_kind=body["storeKind"],
                      body=body)
        if row is None:
            row = KnowledgeRow(id=body["id"], **values)
            self.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        return row

    # ---- operations ----------------------------------------------------------------------------------------
    async def create(self, payload: dict, principal: Principal) -> dict:
        validate_fields(payload, creating=True)
        kb_id = await self.new_id(payload)
        body = {k: v for k, v in payload.items() if k not in READ_ONLY | {"lifecycleStatus"}}
        body.update({"@type": "Knowledge", "id": kb_id, "href": self.href(kb_id),
                     "storeKind": payload.get("storeKind", "hybrid"), "lifecycleStatus": "draft",
                     "conformsTo": await self.resolve_conforms_to(payload.get("conformsTo") or [])})
        parties = [p for p in body.get("relatedParty") or [] if p.get("role") != "owner"]
        if SYSTEM in principal.roles and EDITOR not in principal.roles and principal.client:
            parties.append(owner_party(principal.client))
        if parties:
            body["relatedParty"] = parties
        else:
            body.pop("relatedParty", None)
        if body["storeKind"] in ("vector", "hybrid"):
            await self.vectors.create(kb_id)
        try:
            row = self.save(None, body)
            await self.session.commit()
        except Exception:
            if body["storeKind"] in ("vector", "hybrid"):
                await self.vectors.drop(kb_id)
            raise
        await self.publish("Create", body)
        return await self.body(row)

    async def patch(self, kb_id: str, payload: dict, principal: Principal) -> dict:
        row = await self.get_row(kb_id)
        body = dict(row.body)
        if "lifecycleStatus" in payload and payload["lifecycleStatus"] != row.lifecycle_status:
            if set(payload) - {"lifecycleStatus", "@type"}:
                raise bad_request("Change lifecycleStatus on its own, without other fields")
            return await self.transition(row, payload["lifecycleStatus"], principal)
        self.check_writer(row, principal)
        self.check_writable(row)
        if "storeKind" in payload and payload["storeKind"] != row.store_kind:
            raise bad_request("storeKind cannot change once the knowledge-base exists")
        validate_fields(payload, creating=False)
        changes = {k: v for k, v in payload.items() if k not in READ_ONLY | {"lifecycleStatus", "@type", "storeKind"}}
        if "conformsTo" in changes:
            changes["conformsTo"] = await self.resolve_conforms_to(changes["conformsTo"] or [])
        if "relatedParty" in changes:  # the owner recorded at creation is kept
            changes["relatedParty"] = [p for p in changes["relatedParty"] or [] if p.get("role") != "owner"] \
                + owners(body)
        body.update(changes)
        self.save(row, body)
        await self.session.commit()
        await self.publish("AttributeValueChange", body)
        return await self.body(row)

    async def transition(self, row: KnowledgeRow, target: str, principal: Principal) -> dict:
        lifecycle = self.lifecycles["Knowledge"]
        current = row.lifecycle_status
        transition = lifecycle.find(current, target)
        if transition is None:
            raise conflict(f"No transition from {current} to {target}")
        if transition.role not in principal.roles:
            raise forbidden(f"The transition {current} -> {target} needs the role {transition.role}")
        body = {**row.body, "lifecycleStatus": target}
        self.save(row, body)
        self.session.add(StateTransition(target_kind="knowledge", target_id=row.id, from_state=current,
                                         to_state=target, actor=principal.username))
        await self.session.commit()
        await self.publish("StateChange", body)
        return await self.body(row)

    async def delete(self, kb_id: str, principal: Principal) -> None:
        row = await self.get_row(kb_id)
        self.check_writer(row, principal)
        body = row.body
        if row.store_kind in ("vector", "hybrid"):
            await self.vectors.drop(kb_id)
        await self.session.delete(row)  # documents, blobs, processes cascade
        await self.session.commit()
        for graph in self.graphs.kb_graphs(kb_id):
            self.graphs.remove(graph)
        await self.publish("Delete", body)

    async def write_graph(self, kb_id: str, principal: Principal, action: str, data: bytes = b"",
                          media_type: str = "text/turtle") -> None:
        row = await self.get_row(kb_id)
        self.check_writer(row, principal)
        self.check_writable(row)
        graph = kb_graph(kb_id)
        if action == "merge":
            self.graphs.merge(graph, data, media_type)
        else:  # replace and clear act on the whole knowledge-base, its per-source graphs included
            sources = self.graphs.kb_graphs(kb_id)[1:]
            if action == "replace":
                self.graphs.replace(graph, data, media_type)  # parsed first: invalid RDF changes nothing
            else:
                self.graphs.clear(graph)
            for source in sources:
                self.graphs.remove(source)
        body = dict(row.body)
        self.save(row, body)
        await self.session.commit()
        await self.publish("AttributeValueChange", body)
