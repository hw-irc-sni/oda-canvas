"""Knowledge recommendation (KM-FX-06): rank entities and chunks for a context.

Candidates: the chunks hybrid search finds for ``context`` (not for a graph KB), the entities they mention, and the
entities one hop from the context ``iri``s. Each item gets

    score = relevance × (0.5 + 0.5 × applicability) × (0.5 + successRate)

- relevance ∈ [0, 1]: the search score (an entity takes its best chunk's); a neighbour of a context IRI gets 1;
- applicability: the share of the context IRIs (and their classes) the item is connected to, from the entity's
  profile (stored by KM-FX-06, or computed from the graph);
- successRate = (successes + 1) / (uses + 2), aggregated live from the feedback, so feedback changes the next
  ranking at once. Usage breaks ties, then the item id: the ranking is deterministic.
"""
import re
import secrets
from datetime import datetime, timedelta, timezone

from pyoxigraph import NamedNode
from sqlalchemy import case, delete, func, select

from ..db.models import Recommendation, RecommendFeedback, RecommendProfile
from ..errors import ApiError

IRI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"{}|^`\\]+$")
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
KEEP_DAYS = 30
NOT_ENTITIES = ("urn:tmfc060:process:", "urn:tmfc060:task:", "urn:tmfc060:worker:")


def bad_request(message: str) -> ApiError:
    return ApiError(400, "400", "Bad Request", message)


def is_entity(iri: str) -> bool:
    return not iri.startswith(NOT_ENTITIES) and ":chunk:" not in iri and ":document:" not in iri


def values(iris) -> str:
    return " ".join(f"<{i}>" for i in sorted(iris))


def query(store, dataset: list[str], sparql: str):
    return store.query(sparql, default_graph=[NamedNode(g) for g in dataset])


def neighbours(store, dataset, iris: list[str]) -> set[str]:
    if not iris:
        return set()
    rows = query(store, dataset, f"""SELECT DISTINCT ?n WHERE {{ VALUES ?x {{ {values(iris)} }}
        {{ ?x ?p ?n }} UNION {{ ?n ?p ?x }} FILTER(isIRI(?n) && ?n != ?x && ?p != <{RDF_TYPE}>) }}""")
    return {r["n"].value for r in rows if is_entity(r["n"].value)} - set(iris)


def classes(store, dataset, iris: list[str]) -> set[str]:
    if not iris:
        return set()
    rows = query(store, dataset, f"""SELECT DISTINCT ?c WHERE {{ VALUES ?x {{ {values(iris)} }}
        ?x a/<{RDFS}subClassOf>* ?c FILTER(isIRI(?c)) }}""")
    return {r["c"].value for r in rows}


def profile_of(store, dataset, iri: str) -> set[str]:
    """What an entity is connected to: its neighbours, its types and their superclasses."""
    return neighbours(store, dataset, [iri]) | classes(store, dataset, [iri])


def labels(store, dataset, iris) -> dict[str, str]:
    if not iris:
        return {}
    rows = query(store, dataset, f"""SELECT ?x ?l WHERE {{ VALUES ?x {{ {values(iris)} }}
        ?x <{RDFS}label>|<http://www.w3.org/2004/02/skos/core#prefLabel> ?l }}""")
    out = {}
    for r in rows:
        out.setdefault(r["x"].value, r["l"].value)
    return out


async def signals(session, kb_id: str) -> dict[str, tuple[int, int]]:
    """item -> (uses, successes), live from the feedback."""
    rows = await session.execute(select(
        RecommendFeedback.item_id,
        func.sum(case((RecommendFeedback.used.is_(True), 1), else_=0)),
        func.sum(case((RecommendFeedback.outcome == "success", 1), else_=0)))
        .where(RecommendFeedback.knowledge_id == kb_id).group_by(RecommendFeedback.item_id))
    return {item: (int(uses or 0), int(successes or 0)) for item, uses, successes in rows}


async def recommend(session, kbs, row, payload: dict, dataset: list[str], search) -> dict:
    context = payload.get("context") or ""
    iris = payload.get("iri") or []
    top_k = payload.get("topK", 5)
    if not isinstance(context, str):
        raise bad_request("context must be text")
    if not isinstance(iris, list) or not all(isinstance(i, str) and IRI.match(i) for i in iris):
        raise bad_request("iri must be a list of IRIs")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 100:
        raise bad_request("topK must be an integer from 1 to 100")
    if not context.strip() and not iris:
        raise bad_request("give a context, context IRIs, or both")
    store = kbs.graphs.store
    items: dict[str, dict] = {}

    def candidate(item_id, kind, relevance, **extra):
        item = items.setdefault(item_id, {"itemId": item_id, "kind": kind, "relevance": 0.0, **extra})
        item["relevance"] = max(item["relevance"], relevance)
        return item

    if context.strip() and row.store_kind != "graph":
        for hit in await search(context, max(20, top_k * 4)):
            chunk = candidate(f"chunk:{hit['chunkId']}", "chunk", hit["score"], label=hit["text"][:120],
                              entities=set(hit.get("entityIri") or []))
            for iri in chunk["entities"]:
                if is_entity(iri):
                    candidate(iri, "entity", hit["score"], iri=iri)
    for iri in neighbours(store, dataset, iris):
        candidate(iri, "entity", 1.0, iri=iri)
    if not items:
        result = {"recommendationId": f"rec-{secrets.token_hex(6)}", "items": []}
        session.add(Recommendation(id=result["recommendationId"], knowledge_id=row.id, items=[]))
        await session.commit()
        return result
    wanted = set(iris) | classes(store, dataset, iris)
    entity_iris = {i["iri"] for i in items.values() if i["kind"] == "entity"} | \
        {e for i in items.values() if i["kind"] == "chunk" for e in i["entities"]}
    stored = {p.entity_iri: set(p.iris) for p in (await session.scalars(select(RecommendProfile).where(
        RecommendProfile.knowledge_id == row.id, RecommendProfile.entity_iri.in_(entity_iris)))).all()}

    def profile(iri: str) -> set[str]:
        if iri not in stored:
            stored[iri] = profile_of(store, dataset, iri)
        return stored[iri] | {iri}

    feedback = await signals(session, row.id)
    for item in items.values():
        connected = profile(item["iri"]) if item["kind"] == "entity" else \
            set().union(*(profile(e) for e in item["entities"])) if item["entities"] else set()
        applicability = len(wanted & connected) / len(wanted) if wanted else 0.0
        uses, successes = feedback.get(item["itemId"], (0, 0))
        success_rate = (successes + 1) / (uses + 2)
        item["score"] = round(item["relevance"] * (0.5 + 0.5 * applicability) * (0.5 + success_rate), 6)
        item["signals"] = {"successRate": round(success_rate, 6), "applicability": round(applicability, 6),
                           "usage": uses}
    ranked = sorted(items.values(), key=lambda i: (-i["score"], -i["signals"]["usage"], i["itemId"]))[:top_k]
    names = labels(store, dataset, [i["iri"] for i in ranked if i["kind"] == "entity"])
    out = []
    for i in ranked:
        item = {"itemId": i["itemId"], "kind": i["kind"], "score": i["score"], "signals": i["signals"]}
        if i["kind"] == "entity":
            item["iri"] = i["iri"]
            if i["iri"] in names:
                item["label"] = names[i["iri"]]
        elif i.get("label"):
            item["label"] = i["label"]
        out.append(item)
    rec_id = f"rec-{secrets.token_hex(6)}"
    session.add(Recommendation(id=rec_id, knowledge_id=row.id, items=[i["itemId"] for i in out]))
    await session.execute(delete(Recommendation).where(
        Recommendation.created < datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)))
    await session.commit()
    return {"recommendationId": rec_id, "items": out}


async def feedback(session, kb_id: str, payload: dict, actor: str) -> None:
    rec_id, item_id = payload.get("recommendationId"), payload.get("itemId")
    if not isinstance(rec_id, str) or not isinstance(item_id, str):
        raise bad_request("recommendationId and itemId are required")
    outcome = payload.get("outcome")
    if outcome is not None and outcome not in ("success", "failure", "ignored"):
        raise bad_request("outcome must be success, failure or ignored")
    used = payload.get("used")
    if used is not None and not isinstance(used, bool):
        raise bad_request("used must be true or false")
    if used is None:
        used = outcome in ("success", "failure") if outcome else None
    rec = await session.get(Recommendation, rec_id)
    if rec is None or rec.knowledge_id != kb_id:
        raise ApiError(404, "404", "Not Found", f"No recommendation '{rec_id}' (they are kept {KEEP_DAYS} days)")
    if item_id not in rec.items:
        raise bad_request(f"'{item_id}' was not recommended in {rec_id}")
    await session.execute(delete(RecommendFeedback).where(
        RecommendFeedback.recommendation_id == rec_id, RecommendFeedback.item_id == item_id,
        RecommendFeedback.actor == actor))
    session.add(RecommendFeedback(knowledge_id=kb_id, recommendation_id=rec_id, item_id=item_id, used=used,
                                  outcome=outcome, actor=actor))
    await session.commit()


async def store_profiles(session, kb_id: str, profiles: list[dict]) -> int:
    for p in profiles:
        if not isinstance(p, dict) or not isinstance(p.get("iri"), str) or not isinstance(p.get("iris"), list):
            raise bad_request("profiles are {iri, iris[]}")
        existing = await session.get(RecommendProfile, (kb_id, p["iri"]))
        if existing:
            existing.iris = sorted(set(p["iris"]))
        else:
            session.add(RecommendProfile(knowledge_id=kb_id, entity_iri=p["iri"], iris=sorted(set(p["iris"]))))
    await session.commit()
    return len(profiles)
