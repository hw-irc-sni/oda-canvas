"""KM-FX-06 Knowledge Recommendation: the applicability profiles of the entities just stored.

A profile is what an entity is connected to in the KB: its neighbours (both directions, not through rdf:type), its
types and their superclasses. ``/knowledge/{id}/recommend`` compares it with a context's IRIs (applicability); the
success and usage signals come live from the feedback, so nothing else is precomputed here.
"""
from . import StepError

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
NOT_ENTITIES = ("urn:tmfc060:process:", "urn:tmfc060:task:", "urn:tmfc060:worker:")
BATCH = 100


def is_entity(iri: str) -> bool:
    return not iri.startswith(NOT_ENTITIES) and ":chunk:" not in iri and ":document:" not in iri


def profile_query(iris: list[str]) -> str:
    return f"""SELECT DISTINCT ?x ?n WHERE {{ VALUES ?x {{ {' '.join(f'<{i}>' for i in iris)} }}
      {{ ?x ?p ?n FILTER(?p != <{RDF_TYPE}>) }} UNION {{ ?n ?p ?x FILTER(?p != <{RDF_TYPE}>) }}
      UNION {{ ?x a/<http://www.w3.org/2000/01/rdf-schema#subClassOf>* ?n }}
      FILTER(isIRI(?n) && ?n != ?x) }}"""


def handler(settings=None):
    async def km_fx_06(task: dict, client) -> dict:
        previous = task.get("input") or {}
        if "storeKind" not in previous:
            raise StepError("KM-FX-06 got no KM-FX-05 result", retryable=False)
        kb_id = task["payload"]["kbId"]
        entities = sorted({e["iri"] for e in previous.get("entities") or []} |
                          {i for c in previous.get("chunks") or [] for i in c.get("entityIri") or []})
        entities = [e for e in entities if is_entity(e)]
        profiles: dict[str, set[str]] = {e: set() for e in entities}
        for i in range(0, len(entities), BATCH):
            rows = (await client.query(kb_id, profile_query(entities[i:i + BATCH])))["results"]["bindings"]
            for row in rows:
                if is_entity(row["n"]["value"]):
                    profiles[row["x"]["value"]].add(row["n"]["value"])
        stored = await client.store_profiles(kb_id, [{"iri": e, "iris": sorted(p)} for e, p in profiles.items()]) \
            if profiles else 0
        return {"kind": previous.get("kind"), "documentId": previous.get("documentId"), "profiles": stored,
                "written": previous.get("written", 0), "indexed": previous.get("indexed", 0)}

    return km_fx_06
