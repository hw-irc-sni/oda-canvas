"""Proposed changes to a knowledge-base, made by KM-FX-04 and decided by a person.

- ``dataConflict``: triples withheld because of a SHACL violation or a functional-property clash. Accepting writes
  them to the KB graph.
- ``entityLink``: an extracted entity with several possible existing matches. Accepting with a ``choice`` among the
  candidates writes ``owl:sameAs``.
- ``ontologyTerm``: a class or property the ontology lacks. Read-only here: an ontology-editor raises it on a module.
"""
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from ..db.models import KnowledgeProposal
from ..errors import ApiError
from ..graphstore import kb_graph

KINDS = ("dataConflict", "ontologyTerm", "entityLink")
OWL_SAME_AS = "http://www.w3.org/2002/07/owl#sameAs"


def iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def present(base_path: str, p: KnowledgeProposal) -> dict:
    out = {"@type": "KnowledgeProposal", "id": p.id, "href": f"{base_path}/knowledge/{p.knowledge_id}/proposal/{p.id}",
           "kind": p.kind, "state": p.state, "summary": p.summary, "source": p.source, "processId": p.process_id,
           "details": p.details, "suggestion": p.suggestion, "decidedBy": p.decided_by,
           "decidedAt": iso(p.decided_at), "creationDate": iso(p.created)}
    return {k: v for k, v in out.items() if v is not None}


def nt_term(value: str, kind: str = "iri", extra: str | None = None) -> str:
    if kind == "iri":
        return f"<{value}>"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    if extra and extra.startswith("@"):
        return f'"{escaped}"{extra}'
    return f'"{escaped}"^^<{extra}>' if extra else f'"{escaped}"'


def ntriples(triples: list) -> bytes:
    return "\n".join(f"{nt_term(t[0])} {nt_term(t[1])} {nt_term(t[2], t[3], t[4] if len(t) > 4 else None)} ."
                     for t in triples).encode()


async def record(session, kb_id: str, source: str | None, process_id: str | None, items: list[dict]) -> list[str]:
    """Store proposals from the pipeline, replacing the still undecided ones of the same source."""
    if source:
        await session.execute(delete(KnowledgeProposal).where(
            KnowledgeProposal.knowledge_id == kb_id, KnowledgeProposal.source == source,
            KnowledgeProposal.state == "proposed"))
    ids = []
    start = datetime.now(timezone.utc)
    for n, item in enumerate(items):
        if item.get("kind") not in KINDS or not item.get("summary"):
            raise ApiError(400, "400", "Bad Request", f"A proposal needs a kind among {KINDS} and a summary")
        p = KnowledgeProposal(id=f"kp-{secrets.token_hex(6)}", knowledge_id=kb_id, kind=item["kind"],
                              state="proposed", source=source, process_id=process_id, summary=str(item["summary"]),
                              details=item.get("details") or {}, suggestion=item.get("suggestion"),
                              created=start + timedelta(microseconds=n))  # keeps the pipeline's order
        session.add(p)
        ids.append(p.id)
    await session.commit()
    return ids


async def listing(session, kb_id: str) -> list[KnowledgeProposal]:
    return (await session.scalars(select(KnowledgeProposal).where(KnowledgeProposal.knowledge_id == kb_id)
                                  .order_by(KnowledgeProposal.created, KnowledgeProposal.id))).all()


async def get(session, kb_id: str, proposal_id: str) -> KnowledgeProposal:
    p = await session.get(KnowledgeProposal, proposal_id)
    if p is None or p.knowledge_id != kb_id:
        raise ApiError(404, "404", "Not Found", f"No proposal '{proposal_id}' in '{kb_id}'")
    return p


async def decide(session, graphs, p: KnowledgeProposal, payload: dict, actor: str) -> None:
    state = payload.get("state")
    if state not in ("accepted", "rejected"):
        raise ApiError(400, "400", "Bad Request", "state must be accepted or rejected")
    if p.kind == "ontologyTerm":
        raise ApiError(409, "409", "Conflict", "Ontology terms are decided on an ontology module, not here")
    if p.state != "proposed":
        raise ApiError(409, "409", "Conflict", f"The proposal is already {p.state}")
    if state == "accepted":
        if p.kind == "dataConflict":
            triples = p.details.get("triples") or []
            if triples:
                graphs.merge(kb_graph(p.knowledge_id), ntriples(triples), "text/turtle")
        else:  # entityLink
            choice = payload.get("choice")
            if choice not in (p.details.get("candidates") or []):
                raise ApiError(400, "400", "Bad Request", "choice must be one of the candidates")
            graphs.merge(kb_graph(p.knowledge_id), ntriples([[p.details["iri"], OWL_SAME_AS, choice, "iri"]]),
                         "text/turtle")
            p.details = {**p.details, "choice": choice}
    p.state, p.decided_by, p.decided_at = state, actor, datetime.now(timezone.utc)
    await session.commit()
