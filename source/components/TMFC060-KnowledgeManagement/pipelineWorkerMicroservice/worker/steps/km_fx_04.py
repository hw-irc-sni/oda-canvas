"""KM-FX-04 Knowledge Fusion: SHACL and rule conflicts, OWL-RL reasoning, proposed changes.

- SHACL: the delta is validated (pyshacl) against the shapes modules of the KB's conformsTo closure, with the
  ontology. A violation with a value withholds that triple (focus, path, value); one without a value (for example a
  missing property) withholds the focus node's new triples.
- Functional properties (``owl:FunctionalProperty``): a new value that differs from the KB's existing one (outside
  this document's own source graph), or several new values, are withheld.
- Withheld triples go into ``dataConflict`` proposals (with a model's suggested resolution when a model route is up),
  never into the KB: a person accepts or rejects them. KM-FX-02's unknown terms become ``ontologyTerm`` proposals and
  KM-FX-03's ambiguous matches ``entityLink`` proposals.
- OWL-RL (owlrl) over the ontology and the kept delta; only inferred triples about the delta's subjects are added.
"""
import json

from owlrl import DeductiveClosure, OWLRL_Semantics
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from ..client import ModelRefused, ModelUnavailable
from . import StepError

SH = Namespace("http://www.w3.org/ns/shacl#")
NOISE_TYPES = {OWL.Thing, RDFS.Resource, OWL.NamedIndividual, RDFS.Class, OWL.Class}
SUGGEST = """A knowledge-base ingestion found a conflict. Suggest how a reviewer should resolve it. Answer with one
JSON object: {"action": "keepExisting" | "keepNew" | "keepBoth" | "edit", "rationale": "<one or two sentences>"}."""


def to_node(value: str, kind: str, extra: str | None = None):
    if kind == "iri":
        return URIRef(value)
    if extra and extra.startswith("@"):
        return Literal(value, lang=extra[1:])
    return Literal(value, datatype=URIRef(extra)) if extra else Literal(value)


def to_list(s, p, o) -> list:
    if isinstance(o, Literal):
        extra = f"@{o.language}" if o.language else (str(o.datatype) if o.datatype else None)
        return [str(s), str(p), str(o), "literal", extra]
    return [str(s), str(p), str(o), "iri", None]


def key(t) -> tuple:
    return (t[0], t[1], t[2], t[3], t[4] if len(t) > 4 else None)


def graph_of(triples: list) -> Graph:
    g = Graph()
    for t in triples:
        g.add((URIRef(t[0]), URIRef(t[1]), to_node(t[2], t[3], t[4] if len(t) > 4 else None)))
    return g


def shacl_conflicts(delta: Graph, shapes: Graph, ontology: Graph, triples: list) -> list[dict]:
    from pyshacl import validate
    conforms, report, _ = validate(delta, shacl_graph=shapes, ont_graph=ontology, inference="rdfs",
                                   abort_on_first=False, allow_warnings=True)
    if conforms:
        return []
    conflicts = []
    for result in report.subjects(RDF.type, SH.ValidationResult):
        if report.value(result, SH.resultSeverity) != SH.Violation:
            continue
        focus, path, value = (report.value(result, SH.focusNode), report.value(result, SH.resultPath),
                              report.value(result, SH.value))
        message = str(report.value(result, SH.resultMessage) or "violation")
        if value is not None and isinstance(path, URIRef):
            involved = [t for t in triples if t[0] == str(focus) and t[1] == str(path) and
                        to_node(t[2], t[3], t[4] if len(t) > 4 else None) == value]
        else:
            involved = [t for t in triples if t[0] == str(focus)]
        conflicts.append({"kind": "dataConflict", "summary": f"{focus}: {message}", "details": {
            "triples": involved, "violation": {
                "focusNode": str(focus), "resultPath": str(path) if path is not None else None,
                "value": str(value) if value is not None else None, "message": message,
                "sourceShape": str(report.value(result, SH.sourceShape) or "")}}})
    return conflicts


async def functional_conflicts(client, kb_id: str, source_graph: str, ontology: Graph, triples: list) -> list[dict]:
    functional = {str(p) for p in ontology.subjects(RDF.type, OWL.FunctionalProperty)}
    new: dict[tuple[str, str], list] = {}
    for t in triples:
        if t[1] in functional:
            new.setdefault((t[0], t[1]), []).append(t)
    if not new:
        return []
    pairs = " ".join(f"(<{s}> <{p}>)" for s, p in new)
    query = f"""SELECT ?s ?p ?o WHERE {{ VALUES (?s ?p) {{ {pairs} }} GRAPH ?g {{ ?s ?p ?o }}
      FILTER(?g != <{source_graph}> && !STRSTARTS(STR(?g), "urn:tmfc060:module:")) }}"""
    existing: dict[tuple[str, str], set[str]] = {}
    for b in (await client.query(kb_id, query))["results"]["bindings"]:
        existing.setdefault((b["s"]["value"], b["p"]["value"]), set()).add(b["o"]["value"])
    conflicts = []
    for (s, p), mine in new.items():
        values = {t[2] for t in mine}
        old = existing.get((s, p), set())
        clashing = [t for t in mine if t[2] not in old] if old else (mine if len(values) > 1 else [])
        if (old and values - old) or (not old and len(values) > 1):
            conflicts.append({"kind": "dataConflict",
                              "summary": f"{s}: {p} is functional and would have {len(old | values)} values",
                              "details": {"triples": clashing, "clash": {"subject": s, "property": p,
                                                                         "existing": sorted(old),
                                                                         "new": sorted(values)}}})
    return conflicts


def inferred(ontology: Graph, kept: Graph, limit: int) -> tuple[list, str | None]:
    if len(ontology) + len(kept) > limit:
        return [], f"reasoning skipped: {len(ontology) + len(kept)} triples is over REASONING_MAX_TRIPLES"
    closure = Graph()
    for t in ontology:
        closure.add(t)
    for t in kept:
        closure.add(t)
    DeductiveClosure(OWLRL_Semantics).expand(closure)
    subjects = {s for s in kept.subjects() if isinstance(s, URIRef)}
    out = []
    for s, p, o in closure:
        if s not in subjects or (s, p, o) in kept or (s, p, o) in ontology or isinstance(o, BNode):
            continue
        if (p == RDF.type and o in NOISE_TYPES) or (p == OWL.sameAs and s == o):
            continue
        out.append(to_list(s, p, o))
    return out, None


async def suggest(client, conflict: dict) -> dict | None:
    try:
        content = await client.chat([{"role": "system", "content": SUGGEST},
                                     {"role": "user", "content": json.dumps({"summary": conflict["summary"],
                                                                             **conflict["details"]})}],
                                    response_format={"type": "json_object"}, temperature=0)
        answer = json.loads(content)
    except (ModelUnavailable, ModelRefused, ValueError, TypeError):
        return None
    if isinstance(answer, dict) and answer.get("action") in ("keepExisting", "keepNew", "keepBoth", "edit"):
        return {"action": answer["action"], "rationale": str(answer.get("rationale", ""))[:1000]}
    return None


def handler(settings=None):
    limit = getattr(settings, "reasoning_max_triples", 200_000)

    async def km_fx_04(task: dict, client) -> dict:
        previous = task.get("input") or {}
        if "triples" not in previous:
            raise StepError("KM-FX-04 got no KM-FX-03 result", retryable=False)
        kb_id = task["payload"]["kbId"]
        source = previous.get("documentId") or task.get("chain")
        triples = [key(t) for t in previous["triples"]]
        ontology = Graph().parse(data=await client.ontology(kb_id, "ontology"), format="turtle")
        shapes = Graph().parse(data=await client.ontology(kb_id, "shapes"), format="turtle")
        delta = graph_of(triples)
        conflicts = shacl_conflicts(delta, shapes, ontology, triples) if len(shapes) else []
        conflicts += await functional_conflicts(client, kb_id, f"urn:tmfc060:kb:{kb_id}:source:{source}",
                                                ontology, triples)
        withheld = {key(t) for c in conflicts for t in c["details"]["triples"]}
        kept = [t for t in triples if t not in withheld]
        extra, warning = inferred(ontology, graph_of(kept), limit)
        for c in conflicts:
            c["suggestion"] = await suggest(client, c)
        proposals = conflicts + [
            {"kind": "ontologyTerm", "summary": f"{p['term']} is not in the ontology ({p['role']}, {p['count']}×)",
             "details": p} for p in previous.get("proposedTerms") or []] + [
            {"kind": "entityLink", "summary": f"{c['label']} matches {len(c['candidates'])} entities", "details": c}
            for c in previous.get("linkCandidates") or []]
        await client.propose(kb_id, source, task.get("processId"), [{k: v for k, v in p.items() if v is not None}
                                                                    for p in proposals])
        out = {k: v for k, v in previous.items() if k not in ("triples", "proposedTerms", "linkCandidates")}
        out.update({"triples": [list(t) for t in kept] + extra, "withheld": len(withheld), "inferred": len(extra),
                    "proposals": len(proposals)})
        if warning:
            out["warning"] = warning
        return out

    return km_fx_04
