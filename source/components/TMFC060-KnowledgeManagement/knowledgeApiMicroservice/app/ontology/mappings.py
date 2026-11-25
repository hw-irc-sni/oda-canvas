"""Bridge mappings: reified bridge:mapping records of a bridge module, as in FTO's irc-fto-bridge.ttl.

Each mapping is written to the bridge module's graph twice, like FTO does: the asserted statement
(``subject predicate object``) and a reified record whose node is ``<moduleIRI><id>``::

    <ns>m-kpi a <ns>mapping ; <ns>subject irc:KPI ; <ns>predicate rdfs:subClassOf ; <ns>object an:KPI ;
        <ns>status "proposed" ; <ns>confidence "high" ; <ns>rationale "..." .

The vocabulary (``mapping``, ``subject``, ``predicate``, ``object``, ``status``, ``confidence``, ``rationale``) is in the
module's own namespace, as each FTO bridge file defines it. ``status`` is derived from the lifecycle: ``agreed`` for
agreed and published, ``proposed`` otherwise; a deprecated mapping keeps it and gets ``owl:deprecated true``.

Mappings are edited in any state except deprecated; editing an agreed or published mapping puts it back to draft.
Mappings of a bridge module imported by the federation sync are read-only.
"""
import re
import secrets

from pyoxigraph import Literal, NamedNode, Quad
from sqlalchemy import select

from ..auth import Principal
from ..db.models import KnowledgeSpecification as SpecRow
from ..db.models import Mapping, StateTransition
from ..errors import ApiError
from ..graphstore import module_graph
from .registry import FEDERATED_TYPE, Registry, bad_request, conflict

STANDARD_PREFIXES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
CONFIDENCE = {"high", "medium", "low"}
EDIT_RESET_STATES = {"agreed", "published"}
OWL = "http://www.w3.org/2002/07/owl#"
RDF_TYPE = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
VOCAB = {"mapping": "Class", "subject": "ObjectProperty", "predicate": "ObjectProperty", "object": "ObjectProperty",
         "status": "AnnotationProperty", "confidence": "AnnotationProperty", "rationale": "AnnotationProperty"}


def namespace(module_iri: str) -> str:
    return module_iri if module_iri.endswith(("/", "#")) else module_iri + "#"


def prefix_map(rows) -> dict[str, str]:
    """The standard prefixes plus the prefixes each registered module lists (its ``prefix`` field).

    A listed prefix is bound to the namespace the module's content declares for it (TIO lists ``icm, imo, …``, one
    per sub-namespace); a module listing a single prefix that its content doesn't declare gets its own IRI."""
    out = dict(STANDARD_PREFIXES)
    for row in rows:
        listed = [p.strip() for p in (row.body.get("prefix") or "").split(",") if p.strip()]
        declared = row.namespaces or {}
        for prefix in listed:
            if prefix in declared:
                out.setdefault(prefix, declared[prefix])
            elif len(listed) == 1 and not row.iri.startswith("urn:"):
                out.setdefault(prefix, namespace(row.iri))
    return out


def bridge_status(lifecycle_status: str, previous: str | None = None) -> str:
    if lifecycle_status in ("agreed", "published"):
        return "agreed"
    if lifecycle_status == "deprecated":
        return previous or "proposed"
    return "proposed"


class Mappings:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.session = registry.session
        self.store = registry.graphs.store

    async def bridge_module(self, module_id: str) -> SpecRow:
        row = await self.registry.get_row(module_id)
        if row.spec_kind != "bridge":
            raise conflict(f"'{module_id}' is not a bridge module (specKind {row.spec_kind})")
        return row

    @staticmethod
    def read_only(row: SpecRow) -> None:
        if row.body.get("@type") == FEDERATED_TYPE:
            raise conflict(f"The mappings of '{row.id}' are governed by its source repository")

    async def get(self, module_id: str, mapping_id: str) -> Mapping:
        mapping = await self.session.get(Mapping, (module_id, mapping_id))
        if mapping is None:
            raise ApiError(404, "404", "Not Found", f"No mapping '{mapping_id}' in '{module_id}'")
        return mapping

    async def prefixes(self) -> dict[str, str]:
        return prefix_map((await self.session.scalars(select(SpecRow))).all())

    @staticmethod
    def expand(term: str, prefixes: dict[str, str]) -> str:
        term = term.strip().strip("<>")
        if re.match(r"^[a-z][a-z0-9+.-]*://", term) or term.startswith("urn:"):
            return term
        prefix, sep, local = term.partition(":")
        if sep and prefix in prefixes:
            return prefixes[prefix] + local
        raise bad_request(f"'{term}' is neither an IRI nor a CURIE with a known prefix")

    def present(self, base_path: str, m: Mapping) -> dict:
        out = {"id": m.id, "href": f"{base_path}/knowledgeSpecification/{m.specification_id}/mapping/{m.id}",
               "subject": m.subject, "predicate": m.predicate, "object": m.object, "confidence": m.confidence,
               "rationale": m.rationale, "lifecycleStatus": m.lifecycle_status,
               "status": bridge_status(m.lifecycle_status)}
        return {k: v for k, v in out.items() if v is not None}

    # ---- RDF -----------------------------------------------------------------------------------------------
    def ensure_vocabulary(self, ns: str, graph: NamedNode) -> None:
        for term, kind in VOCAB.items():
            self.store.add(Quad(NamedNode(ns + term), RDF_TYPE, NamedNode(OWL + kind), graph))

    def remove_rdf(self, module: SpecRow, m: Mapping, prefixes: dict[str, str]) -> None:
        graph = NamedNode(module_graph(module.id))
        node = NamedNode(namespace(module.iri) + m.id)
        for quad in list(self.store.quads_for_pattern(node, None, None, graph)):
            self.store.remove(quad)
        try:
            asserted = Quad(NamedNode(self.expand(m.subject, prefixes)), NamedNode(self.expand(m.predicate, prefixes)),
                            NamedNode(self.expand(m.object, prefixes)), graph)
            self.store.remove(asserted)
        except ApiError:
            pass

    def write_rdf(self, module: SpecRow, m: Mapping, prefixes: dict[str, str], previous_status: str | None) -> None:
        ns = namespace(module.iri)
        graph = NamedNode(module_graph(module.id))
        self.ensure_vocabulary(ns, graph)
        node = NamedNode(ns + m.id)
        s, p, o = (NamedNode(self.expand(x, prefixes)) for x in (m.subject, m.predicate, m.object))
        self.store.add(Quad(s, p, o, graph))
        triples = [(RDF_TYPE, NamedNode(ns + "mapping")), (NamedNode(ns + "subject"), s),
                   (NamedNode(ns + "predicate"), p), (NamedNode(ns + "object"), o),
                   (NamedNode(ns + "status"), Literal(bridge_status(m.lifecycle_status, previous_status)))]
        if m.confidence:
            triples.append((NamedNode(ns + "confidence"), Literal(m.confidence)))
        if m.rationale:
            triples.append((NamedNode(ns + "rationale"), Literal(m.rationale)))
        if m.lifecycle_status == "deprecated":
            triples.append((NamedNode(OWL + "deprecated"),
                            Literal("true", datatype=NamedNode("http://www.w3.org/2001/XMLSchema#boolean"))))
        for predicate, obj in triples:
            self.store.add(Quad(node, predicate, obj, graph))

    def current_status(self, module: SpecRow, mapping_id: str) -> str | None:
        node = NamedNode(namespace(module.iri) + mapping_id)
        quads = list(self.store.quads_for_pattern(node, NamedNode(namespace(module.iri) + "status"), None,
                                                  NamedNode(module_graph(module.id))))
        return quads[0].object.value if quads else None

    # ---- operations ----------------------------------------------------------------------------------------
    def validate_fields(self, payload: dict, *, creating: bool) -> None:
        if creating:
            for field in ("subject", "predicate", "object"):
                if not payload.get(field):
                    raise bad_request(f"'{field}' is required")
        if payload.get("confidence") is not None and payload["confidence"] not in CONFIDENCE:
            raise bad_request("confidence must be high, medium or low")

    async def create(self, module_id: str, payload: dict) -> Mapping:
        module = await self.bridge_module(module_id)
        self.read_only(module)
        self.validate_fields(payload, creating=True)
        prefixes = await self.prefixes()
        for field in ("subject", "predicate", "object"):
            self.expand(payload[field], prefixes)
        mapping_id = payload.get("id")
        if mapping_id:
            if not ID_PATTERN.match(mapping_id):
                raise bad_request("id must be 1-63 letters, digits, '.', '_' or '-'")
            if await self.session.get(Mapping, (module_id, mapping_id)):
                raise conflict(f"mapping '{mapping_id}' already exists in '{module_id}'")
        else:
            mapping_id = f"m-{secrets.token_hex(3)}"
        m = Mapping(id=mapping_id, specification_id=module_id, subject=payload["subject"],
                    predicate=payload["predicate"], object=payload["object"], confidence=payload.get("confidence"),
                    rationale=payload.get("rationale"), lifecycle_status="draft")
        self.session.add(m)
        await self.session.commit()
        self.write_rdf(module, m, prefixes, None)
        await self.registry.publish("AttributeValueChange", module.body)
        return m

    async def patch(self, module_id: str, mapping_id: str, payload: dict, principal: Principal) -> Mapping:
        module = await self.bridge_module(module_id)
        self.read_only(module)
        m = await self.get(module_id, mapping_id)
        if "ontology-editor" not in principal.roles:
            raise ApiError(403, "403", "Forbidden", "Only an ontology-editor may change a mapping")
        if m.lifecycle_status == "deprecated":
            raise conflict("A deprecated mapping cannot be edited")
        self.validate_fields(payload, creating=False)
        prefixes = await self.prefixes()
        changes = {k: payload[k] for k in ("subject", "predicate", "object", "confidence", "rationale") if k in payload}
        for field in ("subject", "predicate", "object"):
            if field in changes:
                self.expand(changes[field], prefixes)
        self.remove_rdf(module, m, prefixes)
        for key, value in changes.items():
            setattr(m, key, value)
        if changes and m.lifecycle_status in EDIT_RESET_STATES:
            self.session.add(StateTransition(target_kind="mapping", target_id=f"{module_id}/{mapping_id}",
                                             from_state=m.lifecycle_status, to_state="draft",
                                             actor=principal.username))
            m.lifecycle_status = "draft"
        await self.session.commit()
        self.write_rdf(module, m, prefixes, None)
        await self.registry.publish("AttributeValueChange", module.body)
        return m

    async def delete(self, module_id: str, mapping_id: str, principal: Principal) -> None:
        module = await self.bridge_module(module_id)
        self.read_only(module)
        m = await self.get(module_id, mapping_id)
        if m.lifecycle_status not in ("draft", "rejected") and "ontology-approver" not in principal.roles:
            raise ApiError(403, "403", "Forbidden", f"Only an ontology-approver may delete a {m.lifecycle_status} mapping")
        self.remove_rdf(module, m, await self.prefixes())
        await self.session.delete(m)
        await self.session.commit()
        await self.registry.publish("AttributeValueChange", module.body)

    async def import_from_content(self, module: SpecRow) -> int:
        """Read the reified records of a (synced) bridge module's content into the mapping table."""
        ns = namespace(module.iri)
        graph = NamedNode(module_graph(module.id))
        records = {}
        for quad in self.store.quads_for_pattern(None, RDF_TYPE, NamedNode(ns + "mapping"), graph):
            node = quad.subject
            if not isinstance(node, NamedNode) or not node.value.startswith(ns):
                continue
            values = {}
            for term in ("subject", "predicate", "object", "status", "confidence", "rationale"):
                found = list(self.store.quads_for_pattern(node, NamedNode(ns + term), None, graph))
                if found:
                    values[term] = found[0].object.value
            if {"subject", "predicate", "object"} <= values.keys():
                records[node.value[len(ns):]] = values
        existing = {m.id: m for m in (await self.session.scalars(select(Mapping).where(
            Mapping.specification_id == module.id))).all()}
        for mid, values in records.items():
            lifecycle = "published" if values.get("status") == "agreed" else "proposed"
            m = existing.get(mid) or Mapping(id=mid, specification_id=module.id)
            m.subject, m.predicate, m.object = values["subject"], values["predicate"], values["object"]
            m.confidence = values.get("confidence") if values.get("confidence") in CONFIDENCE else None
            m.rationale, m.lifecycle_status = values.get("rationale"), lifecycle
            if mid not in existing:
                self.session.add(m)
        for mid, m in existing.items():
            if mid not in records:
                await self.session.delete(m)
        return len(records)
