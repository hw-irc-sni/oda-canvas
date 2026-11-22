"""The ontology module registry: knowledgeSpecification CRUD, content, and owl:imports resolution.

Metadata lives in the ``knowledge_specification`` table (indexed columns plus the served body); content lives in
the module's named graph. ``owl:imports`` resolve against the registered modules by IRI, with the semantics of
FTO's catalog.json (an IRI maps to one module). An import that matches no registered module is skipped and
logged; validation (#27) reports it.
"""
import logging
import re
import secrets
from datetime import datetime, timezone

from pyoxigraph import NamedNode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal
from ..config import Settings
from ..db.models import Knowledge as KnowledgeRow
from ..db.models import KnowledgeSpecification as SpecRow
from ..db.models import ValidationReport
from ..errors import ApiError
from ..events import Event
from ..graphstore import GraphStore, module_graph
from . import loaders

log = logging.getLogger(__name__)

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
LAYERS = {"Foundational", "Telecom", "Implementation"}
SPEC_KINDS = {"ontology", "vocabulary", "shapes", "bridge", "remote"}
SOURCE_KINDS = {"upload", "git", "mount", "componentContribution", "remoteEndpoint"}
EDITABLE_STATES = {"draft", "rejected"}
OWL_IMPORTS = NamedNode("http://www.w3.org/2002/07/owl#imports")
FEDERATED_TYPE = "FederatedKnowledgeSpecification"
READ_ONLY = {"id", "href", "lastUpdate", "@baseType", "@schemaLocation", "sourceStatus"}


def bad_request(message: str) -> ApiError:
    return ApiError(400, "400", "Bad Request", message)


def conflict(message: str) -> ApiError:
    return ApiError(409, "409", "Conflict", message)


def not_found(module_id: str) -> ApiError:
    return ApiError(404, "404", "Not Found", f"No knowledgeSpecification '{module_id}'")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:56]
    return slug or "module"


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_source(source) -> dict:
    if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
        raise bad_request(f"source.kind must be one of {', '.join(sorted(SOURCE_KINDS))}")
    required = {"git": ("url", "path"), "mount": ("path",), "remoteEndpoint": ("url",)}.get(source["kind"], ())
    missing = [f for f in required if not source.get(f)]
    if missing:
        raise bad_request(f"source.kind {source['kind']} needs {', '.join(missing)}")
    return source


def validate_fields(body: dict, *, creating: bool) -> None:
    if creating:
        for field in ("name", "iri", "specKind", "source"):
            if not body.get(field):
                raise bad_request(f"'{field}' is required")
    if "layer" in body and body["layer"] is not None and body["layer"] not in LAYERS:
        raise bad_request(f"layer must be one of {', '.join(sorted(LAYERS))}")
    if "specKind" in body and body["specKind"] not in SPEC_KINDS:
        raise bad_request(f"specKind must be one of {', '.join(sorted(SPEC_KINDS))}")
    if "source" in body:
        validate_source(body["source"])
    if body.get("specKind") == "remote" and body.get("source", {}).get("kind") not in (None, "remoteEndpoint"):
        raise bad_request("a remote module needs source.kind remoteEndpoint")


class Registry:
    def __init__(self, session: AsyncSession, graphs: GraphStore, settings: Settings, events):
        self.session = session
        self.graphs = graphs
        self.settings = settings
        self.events = events

    # ---- helpers -------------------------------------------------------------------------------------------
    # hrefs are stored as paths; responses make them absolute with the request's base URL (listing.absolutize)
    def href(self, module_id: str) -> str:
        return f"{self.settings.base_path}/knowledgeSpecification/{module_id}"

    def schema_location(self) -> str:
        return f"{self.settings.base_path}/schema/{FEDERATED_TYPE}.schema.json"

    async def get_row(self, module_id: str) -> SpecRow:
        row = await self.session.get(SpecRow, module_id)
        if row is None:
            raise not_found(module_id)
        return row

    async def all_bodies(self) -> list[dict]:
        rows = (await self.session.scalars(select(SpecRow).order_by(SpecRow.id))).all()
        return [r.body for r in rows]

    async def new_id(self, body: dict) -> str:
        if body.get("id"):
            if not ID_PATTERN.match(body["id"]):
                raise bad_request("id must match ^[a-z0-9][a-z0-9-]{0,62}$")
            if await self.session.get(SpecRow, body["id"]):
                raise conflict(f"knowledgeSpecification '{body['id']}' already exists")
            return body["id"]
        base = slugify(body.get("prefix") or body["name"])
        candidate = base
        while await self.session.get(SpecRow, candidate):
            candidate = f"{base}-{secrets.token_hex(2)}"
        return candidate

    async def resolve_refs(self, refs: list) -> list[dict]:
        """KnowledgeSpecificationRefs by id or iri -> refs of registered modules; unresolved ones are logged."""
        resolved = []
        for ref in refs or []:
            row = None
            if ref.get("id"):
                row = await self.session.get(SpecRow, ref["id"])
            if row is None and ref.get("iri"):
                row = await self.session.scalar(select(SpecRow).where(SpecRow.iri == ref["iri"]))
            if row is None:
                log.warning("unresolved module reference %s", ref.get("iri") or ref.get("id"))
                continue
            if all(r["id"] != row.id for r in resolved):
                resolved.append({"@type": "KnowledgeSpecificationRef", "id": row.id, "href": self.href(row.id),
                                 "iri": row.iri, "name": row.name})
        return resolved

    def content_imports(self, module_id: str) -> list[dict]:
        """The owl:imports IRIs declared in the module's content, as refs to resolve."""
        quads = self.graphs.store.quads_for_pattern(None, OWL_IMPORTS, None, NamedNode(module_graph(module_id)))
        return [{"iri": q.object.value} for q in quads if isinstance(q.object, NamedNode)]

    async def refresh_imports(self, body: dict) -> None:
        declared = list(body.get("imports") or []) + self.content_imports(body["id"])
        body["imports"] = [r for r in await self.resolve_refs(declared) if r["id"] != body["id"]]

    async def load_source_content(self, body: dict) -> None:
        source = body["source"]
        if source["kind"] == "git":
            data = await loaders.read_git_file(self.settings, source)
        elif source["kind"] == "mount":
            data = loaders.read_mount_file(self.settings, source)
        else:
            return  # upload and componentContribution arrive through PUT /content; remoteEndpoint has none
        self.graphs.replace(module_graph(body["id"]), data, "text/turtle")

    def save(self, row: SpecRow | None, body: dict) -> SpecRow:
        body["lastUpdate"] = now()
        values = dict(name=body["name"], iri=body["iri"], spec_kind=body["specKind"], layer=body.get("layer"),
                      lifecycle_status=body["lifecycleStatus"], link_status=body.get("linkStatus"), body=body)
        if row is None:
            row = SpecRow(id=body["id"], **values)
            self.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        return row

    async def publish(self, kind: str, body: dict) -> None:
        await self.events.publish(Event(f"KnowledgeSpecification{kind}Event", "knowledgeSpecification", body))

    # ---- operations ----------------------------------------------------------------------------------------
    async def create(self, payload: dict, *, lifecycle_status: str = "draft", extra: dict | None = None) -> dict:
        validate_fields(payload, creating=True)
        if await self.session.scalar(select(SpecRow).where(SpecRow.iri == payload["iri"])):
            raise conflict(f"a module with iri {payload['iri']} already exists")
        module_id = await self.new_id(payload)
        body = {k: v for k, v in payload.items() if k not in READ_ONLY | {"lifecycleStatus"}}
        body.update({"@type": payload.get("@type") or "KnowledgeSpecification", "id": module_id,
                     "href": self.href(module_id), "lifecycleStatus": lifecycle_status, **(extra or {})})
        if body["@type"] == FEDERATED_TYPE:
            body.update({"@baseType": "KnowledgeSpecification", "@schemaLocation": self.schema_location()})
        try:
            await self.load_source_content(body)
        except ApiError:
            self.graphs.remove(module_graph(module_id))
            raise
        await self.refresh_imports(body)
        self.save(None, body)
        await self.session.commit()
        await self.publish("Create", body)
        return body

    async def patch(self, module_id: str, payload: dict, principal: Principal) -> dict:
        row = await self.get_row(module_id)
        body = dict(row.body)
        if "linkStatus" in payload and payload["linkStatus"] != body.get("linkStatus"):
            raise bad_request("linkStatus is set by the ontology-approver when ruling")
        if "ontology-editor" not in principal.roles:
            raise ApiError(403, "403", "Forbidden", "Only an ontology-editor may change a module's fields")
        if "iri" in payload and payload["iri"] != body["iri"]:
            raise bad_request("iri cannot change")
        if body["lifecycleStatus"] not in EDITABLE_STATES:
            raise conflict(f"a {body['lifecycleStatus']} module cannot be edited")
        validate_fields(payload, creating=False)
        changes = {k: v for k, v in payload.items() if k not in READ_ONLY | {"lifecycleStatus", "@type", "linkStatus"}}
        body.update(changes)
        if "source" in changes:
            await self.load_source_content(body)
        await self.refresh_imports(body)
        self.save(row, body)
        await self.session.commit()
        await self.publish("AttributeValueChange", body)
        return body

    async def delete(self, module_id: str, principal: Principal) -> None:
        row = await self.get_row(module_id)
        if row.lifecycle_status not in EDITABLE_STATES and "ontology-approver" not in principal.roles:
            raise ApiError(403, "403", "Forbidden",
                           f"Only an ontology-approver may delete a {row.lifecycle_status} module")
        users = [kb.id for kb in (await self.session.scalars(select(KnowledgeRow))).all()
                 if any(r.get("id") == module_id for r in kb.body.get("conformsTo") or [])]
        importers = [b["id"] for b in await self.all_bodies()
                     if any(r.get("id") == module_id for r in b.get("imports") or [])]
        if users or importers:
            raise conflict(f"in use by knowledge-bases {users} and modules {importers}")
        body = row.body
        await self.session.delete(row)
        await self.session.commit()
        self.graphs.remove(module_graph(module_id))
        await self.publish("Delete", body)

    async def put_content(self, module_id: str, data: bytes, media_type: str, principal: Principal) -> None:
        row = await self.get_row(module_id)
        source = row.body["source"]
        if source["kind"] == "upload":
            allowed = "ontology-editor" in principal.roles
        elif source["kind"] == "componentContribution":
            contributor = source.get("componentName") or source.get("componentId")
            allowed = "canvasSystemRole" in principal.roles and principal.client == contributor
        else:
            raise conflict(f"a module from {source['kind']} gets its content from that source")
        if not allowed:
            raise ApiError(403, "403", "Forbidden", "Not allowed to replace this module's content")
        if row.lifecycle_status not in EDITABLE_STATES:
            raise conflict(f"a {row.lifecycle_status} module cannot be edited")
        self.graphs.replace(module_graph(module_id), data, media_type)
        body = dict(row.body)
        await self.refresh_imports(body)
        self.save(row, body)
        await self.session.commit()
        await self.publish("AttributeValueChange", body)

    # ---- validation (#27) ------------------------------------------------------------------------------------
    def rdflib_graph(self, module_id: str):
        from rdflib import Graph
        data = self.graphs.export(module_graph(module_id), "text/turtle")
        return Graph().parse(data=data, format="turtle")

    async def import_closure(self, module_id: str, bodies: dict[str, dict]) -> list[str]:
        seen, stack = [], [r["id"] for r in bodies[module_id].get("imports") or []]
        while stack:
            current = stack.pop()
            if current in seen or current == module_id or current not in bodies:
                continue
            seen.append(current)
            stack.extend(r["id"] for r in bodies[current].get("imports") or [])
        return seen

    async def validate(self, module_id: str) -> dict:
        from rdflib import Graph

        from .checks import check_module, report, shacl_validate
        row = await self.get_row(module_id)
        bodies = {b["id"]: b for b in await self.all_bodies()}
        graphs = {mid: self.rdflib_graph(mid) for mid in bodies}
        union = Graph()
        for graph in graphs.values():
            union += graph
        # namespaces whose classes can be checked: modules that have content
        iris = [b["iri"] for mid, b in bodies.items() if not b["iri"].startswith("urn:") and len(graphs[mid])]
        unresolved = [r["iri"] for r in self.content_imports(module_id) if r["iri"] not in {b["iri"] for b in bodies.values()}]
        rules = check_module(row.body, graphs[module_id], union, iris, unresolved)
        closure = await self.import_closure(module_id, bodies)
        shapes, ontology = Graph(), Graph()
        for mid in closure:
            if bodies[mid].get("specKind") == "shapes":
                shapes += graphs[mid]
            else:
                ontology += graphs[mid]
        data = graphs[module_id] if row.body.get("specKind") != "shapes" else Graph()
        result = report(module_id, rules, shacl_validate(data, shapes, ontology))
        stored = await self.session.get(ValidationReport, module_id)
        if stored is None:
            self.session.add(ValidationReport(specification_id=module_id, report=result))
        else:
            stored.report = result
        await self.session.commit()
        return result

    async def last_report(self, module_id: str) -> dict:
        await self.get_row(module_id)
        stored = await self.session.get(ValidationReport, module_id)
        if stored is None:
            raise ApiError(404, "404", "Not Found", f"'{module_id}' has not been validated yet")
        return stored.report
