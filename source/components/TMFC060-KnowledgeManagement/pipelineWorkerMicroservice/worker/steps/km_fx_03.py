"""KM-FX-03 Knowledge Generation: entity linking and PROV-O provenance.

An extracted entity is linked to an existing KB entity when exactly one has the same normalised label (rdfs:label,
skos:prefLabel or skos:altLabel) and the same class, a subclass or a superclass; its IRI is then replaced by the
existing one everywhere. Several matches link nothing and are reported as ``linkCandidates``.

Provenance (in the result's triples, so it is written to the document's source graph with the facts):
entity ``prov:wasDerivedFrom`` chunk, chunk ``prov:wasDerivedFrom`` document (TMF: ``prov:hadPrimarySource`` the
resource), entity ``prov:wasGeneratedBy`` the ingestion process (a ``prov:Activity`` associated with the worker and
informed by this task) and ``prov:generatedAtTime``.
"""
import re
import unicodedata
from datetime import datetime, timezone

from . import StepError
from .vocabulary import RDF, RDFS, SKOS

PROV = "http://www.w3.org/ns/prov#"
XSD_DATETIME = "http://www.w3.org/2001/XMLSchema#dateTime"
LABELS = (RDFS + "label", SKOS + "prefLabel", SKOS + "altLabel")
CANDIDATES = """SELECT DISTINCT ?e ?label ?c0 WHERE {{
  VALUES ?c0 {{ {classes} }}
  VALUES ?lp {{ {labels} }}
  ?e a ?c ; ?lp ?label .
  {{ ?c <http://www.w3.org/2000/01/rdf-schema#subClassOf>* ?c0 }} UNION
  {{ ?c0 <http://www.w3.org/2000/01/rdf-schema#subClassOf>* ?c }}
  FILTER(isIRI(?e))
}}"""


def normalise(label: str) -> str:
    text = unicodedata.normalize("NFKD", label)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def kb_node(kb_id: str, kind: str, key: str) -> str:
    return f"urn:tmfc060:kb:{kb_id}:{kind}:{key}"


async def find_links(client, kb_id: str, entities: list[dict]) -> tuple[dict[str, str], list[dict]]:
    if not entities:
        return {}, []
    classes = sorted({e["type"] for e in entities})
    query = CANDIDATES.format(classes=" ".join(f"<{c}>" for c in classes),
                              labels=" ".join(f"<{p}>" for p in LABELS))
    rows = (await client.query(kb_id, query))["results"]["bindings"]
    index: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        index.setdefault((row["c0"]["value"], normalise(row["label"]["value"])), set()).add(row["e"]["value"])
    new_iris = {e["iri"] for e in entities}
    links, ambiguous = {}, []
    for e in entities:
        found = index.get((e["type"], normalise(e["label"])), set()) - new_iris
        if len(found) == 1:
            links[e["iri"]] = found.pop()
        elif len(found) > 1:
            ambiguous.append({"iri": e["iri"], "label": e["label"], "type": e["type"], "candidates": sorted(found)})
    return links, ambiguous


def handler(settings=None):
    async def km_fx_03(task: dict, client) -> dict:
        previous = task.get("input") or {}
        if "triples" not in previous:
            raise StepError("KM-FX-03 got no KM-FX-02 result", retryable=False)
        kb_id = task["payload"]["kbId"]
        entities = [dict(e) for e in previous.get("entities") or []]
        links, ambiguous = await find_links(client, kb_id, entities)

        def swap(value: str) -> str:
            return links.get(value, value)

        triples = {}
        for t in previous["triples"]:
            s, p, o, kind = t[0], t[1], t[2], t[3]
            extra = t[4] if len(t) > 4 else None
            triples[(swap(s), p, swap(o) if kind == "iri" else o, kind, extra)] = None
        for e in entities:
            e["iri"] = swap(e["iri"])
        chunks = [dict(c, entityIri=sorted({swap(i) for i in c.get("entityIri") or []})) for c in
                  previous.get("chunks") or []]

        process = f"urn:tmfc060:process:{task.get('processId')}"
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        doc_id = previous.get("documentId")
        document = kb_node(kb_id, "document", doc_id) if doc_id else None

        def add(s, p, o, kind="iri", extra=None):
            triples[(s, p, o, kind, extra)] = None

        add(process, RDF + "type", PROV + "Activity")
        add(process, PROV + "wasAssociatedWith", f"urn:tmfc060:worker:{getattr(client, 'worker', 'worker')}")
        add(process, PROV + "wasInformedBy", f"urn:tmfc060:task:{task.get('id')}")
        if document:
            add(document, RDF + "type", PROV + "Entity")
        derived = set()
        for c in chunks:
            if not c.get("entityIri"):
                continue
            chunk = kb_node(kb_id, "chunk", c["chunkId"])
            add(chunk, RDF + "type", PROV + "Entity")
            if document:
                add(chunk, PROV + "wasDerivedFrom", document)
            for iri in c["entityIri"]:
                if previous.get("kind") == "tmf" and iri.startswith(("http://", "https://")):
                    add(chunk, PROV + "hadPrimarySource", iri)
                add(iri, PROV + "wasDerivedFrom", chunk)
                derived.add(iri)
        if previous.get("kind") == "rdf" and document:  # lifted subjects derive from the document itself
            for s, p, *_ in list(triples):
                if p == RDF + "type" and s not in derived and not s.startswith("urn:tmfc060:process:") \
                        and s != document:
                    add(s, PROV + "wasDerivedFrom", document)
                    derived.add(s)
        for iri in sorted(derived):
            add(iri, PROV + "wasGeneratedBy", process)
            add(iri, PROV + "generatedAtTime", now, "literal", XSD_DATETIME)
        out = {k: v for k, v in previous.items() if k not in ("triples", "entities", "chunks")}
        out.update({"triples": [list(t) for t in triples], "entities": entities, "chunks": chunks,
                    "links": links, "linkCandidates": ambiguous, "provenance": len(derived)})
        return out

    return km_fx_03
