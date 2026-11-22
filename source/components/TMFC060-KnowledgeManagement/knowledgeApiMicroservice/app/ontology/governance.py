"""The governed lifecycle of ontology modules, mappings and knowledge-bases.

The allowed transitions, and the role that may make each one, come from the ``x-lifecycle`` extension of the
schemas in the TMFxxx OAS (KnowledgeSpecification, also used by mappings, and Knowledge); the review rules from
``x-review-kinds`` of ReviewItem. A transition:

- needs the role of the transition (403) and must be listed (409);
- for a ruling ``agreed``, needs the module's validation to conform (409), unless GOVERNANCE_REQUIRE_VALIDATION is off;
- is refused for modules governed by a federation repository (409);
- is recorded in ``state_transition``, written back to RDF and published as a StateChange event.

RDF write-back for a module: ``fto:linkStatus`` and ``owl:deprecated`` on its ``owl:Ontology`` node in the module
graph; in the governance graph, ``adms:status`` of the module and one ``prov:Activity`` per ruling.
"""
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pyoxigraph import Literal, NamedNode, Quad
from sqlalchemy import select

from ..auth import Principal
from ..db.models import ReviewItem, StateTransition
from ..errors import ApiError
from ..events import Event
from ..graphstore import GOVERNANCE_GRAPH, module_graph
from .registry import FEDERATED_TYPE, Registry

FTO_LINK_STATUS = NamedNode("https://models.tmforum.org/ontology/fto/linkStatus")
OWL_DEPRECATED = NamedNode("http://www.w3.org/2002/07/owl#deprecated")
ADMS_STATUS = NamedNode("http://www.w3.org/ns/adms#status")
RDF_TYPE = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
PROV = "http://www.w3.org/ns/prov#"
RDFS_COMMENT = NamedNode("http://www.w3.org/2000/01/rdf-schema#comment")
XSD_BOOLEAN = NamedNode("http://www.w3.org/2001/XMLSchema#boolean")
XSD_DATETIME = NamedNode("http://www.w3.org/2001/XMLSchema#dateTime")
LINK_STATUSES = {"provisional", "confirmed"}


@dataclass(frozen=True)
class Transition:
    source: str
    target: str
    role: str
    condition: str | None = None


@dataclass(frozen=True)
class Lifecycle:
    initial: str
    transitions: tuple[Transition, ...]
    review_comments: tuple[str, ...] = ()

    def find(self, source: str, target: str) -> Transition | None:
        return next((t for t in self.transitions if t.source == source and t.target == target), None)


def load_lifecycles(oas_path: str) -> dict[str, Lifecycle]:
    """x-lifecycle of each schema that has one, by schema name."""
    schemas = yaml.safe_load(Path(oas_path).read_text())["components"]["schemas"]
    out = {}
    for name, schema in schemas.items():
        spec = schema.get("x-lifecycle")
        if spec:
            out[name] = Lifecycle(initial=spec["initial"],
                                  transitions=tuple(Transition(t["from"], t["to"], t["role"], t.get("condition"))
                                                    for t in spec["transitions"]),
                                  review_comments=tuple(spec.get("reviewComments") or ()))
    return out


def now() -> datetime:
    return datetime.now(timezone.utc)


def conflict(message: str) -> ApiError:
    return ApiError(409, "409", "Conflict", message)


def forbidden(message: str) -> ApiError:
    return ApiError(403, "403", "Forbidden", message)


class Governance:
    def __init__(self, registry: Registry, lifecycles: dict[str, Lifecycle]):
        self.registry = registry
        self.session = registry.session
        self.graphs = registry.graphs
        self.lifecycles = lifecycles

    # ---- transitions ---------------------------------------------------------------------------------------
    def check(self, lifecycle: Lifecycle, current: str, target: str, principal: Principal) -> Transition:
        transition = lifecycle.find(current, target)
        if transition is None:
            raise conflict(f"No transition from {current} to {target}")
        if transition.role not in principal.roles:
            raise forbidden(f"The transition {current} -> {target} needs the role {transition.role}")
        return transition

    async def transition_module(self, module_id: str, target: str, principal: Principal, *,
                                review_item: ReviewItem | None = None, link_status: str | None = None,
                                validated: bool = False) -> dict:
        row = await self.registry.get_row(module_id)
        body = dict(row.body)
        if body.get("@type") == FEDERATED_TYPE:
            raise conflict(f"'{module_id}' is governed by its source repository")
        current = body["lifecycleStatus"]
        self.check(self.lifecycles["KnowledgeSpecification"], current, target, principal)
        if target == "agreed" and not validated:
            await self.require_valid(module_id)
            row = await self.registry.get_row(module_id)
            body = dict(row.body)
        body["lifecycleStatus"] = target
        if link_status:
            body["linkStatus"] = link_status
        self.registry.save(row, body)
        self.session.add(StateTransition(target_kind="module", target_id=module_id, from_state=current,
                                         to_state=target, actor=principal.username,
                                         review_item_id=review_item.id if review_item else None))
        await self.session.commit()
        self.write_module_rdf(body, review_item)
        await self.registry.events.publish(Event("KnowledgeSpecificationStateChangeEvent", "knowledgeSpecification",
                                                 body))
        return body

    async def require_valid(self, module_id: str) -> None:
        """The validation gate of a ruling agreed (it stores the report, so run it before writing anything)."""
        if not self.registry.settings.governance_require_validation:
            return
        report = await self.registry.validate(module_id)
        if not report["conforms"]:
            failed = [r["rule"] for r in report["rules"] if not r["passed"]]
            raise conflict(f"Validation must pass before agreed: failed rules {failed}, "
                           f"SHACL conforms {report['shacl']['conforms']}")

    def write_module_rdf(self, body: dict, review_item: ReviewItem | None) -> None:
        store = self.graphs.store
        graph = NamedNode(module_graph(body["id"]))
        governance = NamedNode(GOVERNANCE_GRAPH)
        module = NamedNode(body["iri"])
        if body.get("linkStatus"):
            for quad in list(store.quads_for_pattern(module, FTO_LINK_STATUS, None, graph)):
                store.remove(quad)
            store.add(Quad(module, FTO_LINK_STATUS, Literal(body["linkStatus"]), graph))
        if body["lifecycleStatus"] == "deprecated":
            store.add(Quad(module, OWL_DEPRECATED, Literal("true", datatype=XSD_BOOLEAN), graph))
        for quad in list(store.quads_for_pattern(module, ADMS_STATUS, None, governance)):
            store.remove(quad)
        store.add(Quad(module, ADMS_STATUS, NamedNode(f"urn:tmfc060:lifecycle:{body['lifecycleStatus']}"), governance))
        if review_item is not None and review_item.kind == "ruling":
            activity = NamedNode(f"urn:tmfc060:ruling:{review_item.id}")
            for predicate, obj in [
                (RDF_TYPE, NamedNode(PROV + "Activity")),
                (NamedNode(PROV + "used"), module),
                (NamedNode(PROV + "wasAssociatedWith"), NamedNode(f"urn:tmfc060:user:{review_item.approver}")),
                (NamedNode(PROV + "endedAtTime"), Literal(review_item.created.isoformat(), datatype=XSD_DATETIME)),
                (NamedNode(PROV + "value"), Literal(review_item.ruling)),
            ]:
                store.add(Quad(activity, predicate, obj, governance))
            if review_item.text:
                store.add(Quad(activity, RDFS_COMMENT, Literal(review_item.text), governance))

    # ---- review items --------------------------------------------------------------------------------------
    async def create_review(self, module_id: str, payload: dict, principal: Principal) -> dict:
        row = await self.registry.get_row(module_id)
        if row.body.get("@type") == FEDERATED_TYPE:
            raise conflict(f"'{module_id}' is governed by its source repository")
        kind = payload.get("kind")
        target = payload.get("target") or {}
        if kind not in ("proposal", "comment", "ruling"):
            raise ApiError(400, "400", "Bad Request", "kind must be proposal, comment or ruling")
        if target.get("kind") == "mapping":
            raise ApiError(501, "501", "Not Implemented", "Reviews of mappings arrive with the mapping sub-resource")
        if target.get("kind") != "module" or target.get("id") not in (None, module_id):
            raise ApiError(400, "400", "Bad Request", f"target must be the module {module_id}")
        current = row.lifecycle_status
        lifecycle = self.lifecycles["KnowledgeSpecification"]
        item = ReviewItem(id=f"rv-{secrets.token_hex(6)}", specification_id=module_id, target_kind="module",
                          target_id=module_id, kind=kind, text=payload.get("text"), author=principal.username,
                          created=now())
        if kind == "comment":
            if "ontology-reviewer" not in principal.roles:
                raise forbidden("Comments need the role ontology-reviewer")
            if current not in lifecycle.review_comments:
                raise conflict(f"Comments are accepted while {' or '.join(lifecycle.review_comments)}, not {current}")
            self.session.add(item)
            await self.session.commit()
            return self.present(item)
        if kind == "proposal":
            self.check(lifecycle, current, "proposed", principal)
            self.session.add(item)
            await self.session.flush()
            await self.transition_module(module_id, "proposed", principal, review_item=item)
            return self.present(item)
        ruling = payload.get("ruling")
        if ruling not in ("agreed", "rejected"):
            raise ApiError(400, "400", "Bad Request", "A ruling needs ruling agreed or rejected")
        link_status = payload.get("linkStatus")
        if link_status is not None and link_status not in LINK_STATUSES:
            raise ApiError(400, "400", "Bad Request", "linkStatus must be provisional or confirmed")
        self.check(lifecycle, current, ruling, principal)
        if ruling == "agreed":
            await self.require_valid(module_id)
        item.ruling, item.link_status, item.approver = ruling, link_status, principal.username
        self.session.add(item)
        await self.session.flush()
        await self.transition_module(module_id, ruling, principal, review_item=item, link_status=link_status,
                                     validated=True)
        return self.present(item)

    async def list_reviews(self, module_id: str) -> list[dict]:
        await self.registry.get_row(module_id)
        items = (await self.session.scalars(select(ReviewItem).where(ReviewItem.specification_id == module_id)
                                            .order_by(ReviewItem.created))).all()
        return [self.present(i) for i in items]

    @staticmethod
    def present(item: ReviewItem) -> dict:
        out = {"id": item.id, "kind": item.kind, "target": {"kind": item.target_kind, "id": item.target_id},
               "text": item.text, "ruling": item.ruling, "linkStatus": item.link_status, "author": item.author,
               "approver": item.approver, "date": item.created.isoformat().replace("+00:00", "Z")}
        return {k: v for k, v in out.items() if v is not None}
