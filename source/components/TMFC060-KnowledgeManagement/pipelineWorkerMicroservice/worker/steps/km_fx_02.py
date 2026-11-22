"""KM-FX-02 Information Extraction: ontology-constrained LLM extraction for text, direct lifting for RDF and TMF.

Every triple must use the knowledge-base's ontology: classes for ``rdf:type`` and properties as predicates (plus
rdf:type, rdfs:label/comment and SKOS labels). Anything else is dropped and reported in ``proposedTerms`` for
KM-FX-04 to propose to the ontology reviewers.

Result (the next step's ``input``)::

    {"kind", "documentId" | "source", "triples": [[s, p, o, "iri" | "literal", datatype | "@lang" | None]],
     "entities": [{"iri", "type", "label", "chunkIds"}], "chunks": [... with entityIri and classIri],
     "proposedTerms": [{"term", "role": "class" | "predicate", "count", "example"}], "dropped": n}
"""
import hashlib
import json
import re
from collections import Counter

from rdflib import BNode, Graph, Literal, URIRef

from ..client import ModelRefused, ModelUnavailable
from . import StepError
from .vocabulary import (BASE_PREDICATES, KINDS, RDF, RDFS, SCHEMA_PREDICATES, Vocabulary, VocabularyCache,
                         local_name)

RDF_TYPE, RDFS_LABEL = RDF + "type", RDFS + "label"
OPEN_API = "https://models.tmforum.org/ontology/open-api/{api}/"
SYSTEM = """You extract knowledge for a knowledge graph. Use ONLY the classes and properties listed below.
Answer with one JSON object: {"entities": [{"id": "e1", "type": "<class IRI>", "label": "<name as in the text>"}],
"relations": [{"subject": "e1", "predicate": "<property IRI>", "object": "e2"}],
"attributes": [{"subject": "e1", "predicate": "<property IRI>", "value": "<literal>"}]}.
Only state what the text says. If nothing fits the vocabulary, answer {"entities": [], "relations": [], "attributes": []}.

Vocabulary:
"""


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not s or len(s) > 60 or not text.isascii():
        s = f"{s[:40]}-{hashlib.sha1(text.encode()).hexdigest()[:8]}".strip("-")
    return s


def entity_iri(kb_id: str, class_iri: str, label: str) -> str:
    return f"urn:tmfc060:kb:{kb_id}:entity:{local_name(class_iri)}:{slug(label)}"


class Collector:
    """Triples that pass the ontology filter, the dropped terms, and the entities found per chunk."""

    def __init__(self, vocab: Vocabulary):
        self.vocab = vocab
        self.triples: dict[tuple, None] = {}
        self.proposed: Counter = Counter()
        self.examples: dict[tuple, str] = {}
        self.dropped = 0
        self.entities: dict[str, dict] = {}

    def propose(self, term: str, role: str, example: str) -> None:
        self.proposed[(term, role)] += 1
        self.examples.setdefault((term, role), example[:200])
        self.dropped += 1

    def add(self, s: str, p: str, o: str, kind: str, extra=None, example: str = "", local=frozenset()) -> bool:
        """Keep the triple if it uses the ontology (or, for `local`, terms its own document declares)."""
        if p not in self.vocab.declared and p not in BASE_PREDICATES and p not in local:
            self.propose(p, "predicate", example)
            return False
        if p == RDF_TYPE and o not in self.vocab.classes and o not in local:
            self.propose(o, "class", example)
            return False
        self.triples[(s, p, o, kind, extra)] = None
        return True

    def entity(self, iri: str, class_iri: str, label: str, chunk_id: str | None) -> None:
        e = self.entities.setdefault(iri, {"iri": iri, "type": class_iri, "label": label, "chunkIds": []})
        if chunk_id and chunk_id not in e["chunkIds"]:
            e["chunkIds"].append(chunk_id)

    def result(self) -> dict:
        return {"triples": [list(t) for t in self.triples], "entities": list(self.entities.values()),
                "proposedTerms": [{"term": t, "role": r, "count": n, "example": self.examples[(t, r)]}
                                  for (t, r), n in sorted(self.proposed.items())],
                "dropped": self.dropped}


# ---- RDF lifting -----------------------------------------------------------------------------------------------
def remote_contexts(value) -> bool:
    if isinstance(value, dict):
        ctx = value.get("@context")
        if isinstance(ctx, str) or (isinstance(ctx, list) and any(isinstance(c, str) for c in ctx)):
            return True
        return any(remote_contexts(v) for v in value.values())
    if isinstance(value, list):
        return any(remote_contexts(v) for v in value)
    return False


def lift_rdf(collector: Collector, content: str, media_type: str, kb_id: str, origin: str) -> list[str]:
    """Parse Turtle or JSON-LD and keep what fits the ontology; returns the subjects typed by it."""
    if media_type == "application/ld+json":
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise StepError(f"invalid JSON-LD: {exc}", retryable=False) from exc
        if remote_contexts(parsed):
            raise StepError("JSON-LD with remote @context URLs is not fetched; inline the context", retryable=False)
        fmt = "json-ld"
    else:
        fmt = "turtle"
    graph = Graph()
    try:
        graph.parse(data=content, format=fmt)
    except Exception as exc:  # noqa: BLE001 - a broken document will not parse on a retry either
        raise StepError(f"invalid {media_type}: {exc}", retryable=False) from exc
    salt = hashlib.sha1(origin.encode()).hexdigest()[:10]

    def term(node) -> tuple[str, str, str | None]:
        if isinstance(node, BNode):
            return f"urn:tmfc060:kb:{kb_id}:bnode:{salt}:{node}", "iri", None
        if isinstance(node, Literal):
            return str(node), "literal", (f"@{node.language}" if node.language else
                                          (str(node.datatype) if node.datatype else None))
        return str(node), "iri", None

    # schema the document declares is kept with it, and its own terms are valid in it
    local = set(SCHEMA_PREDICATES) | set(KINDS)
    for s, p, o in graph:
        if str(p) == RDF_TYPE and str(o) in KINDS and not isinstance(s, BNode):
            local.add(str(s))
    typed = []
    for s, p, o in graph:
        s_iri, _, _ = term(s)
        o_val, kind, extra = term(o)
        if collector.add(s_iri, str(p), o_val, kind, extra, example=f"{s_iri} {p} {o_val}", local=local) \
                and str(p) == RDF_TYPE:
            typed.append(s_iri)
    return typed


# ---- TMF records -----------------------------------------------------------------------------------------------
def lift_tmf(collector: Collector, record: dict, kb_id: str) -> tuple[str, str | None]:
    resource, api = record["resource"], record["source"]
    ns = OPEN_API.format(api=api)
    rtype = resource.get("@type") or "Resource"
    href = resource.get("href") or ""
    subject = href if re.match(r"^https?://", href) else f"urn:tmf:{api}:{rtype}:{resource.get('id')}"
    class_iri = ns + rtype
    typed = collector.add(subject, RDF_TYPE, class_iri, "iri", example=f"{api} {rtype}")
    for key, value in resource.items():
        if key.startswith("@") or key == "href" or isinstance(value, (dict, list)) or value is None:
            continue
        collector.add(subject, ns + key, str(value), "literal", None, example=f"{rtype}.{key}")
    for key in ("resourceCharacteristic", "serviceCharacteristic", "characteristic"):
        for c in resource.get(key) or []:
            if isinstance(c, dict) and c.get("name") and not isinstance(c.get("value"), (dict, list)):
                collector.add(subject, ns + str(c["name"]), str(c.get("value")), "literal", None,
                              example=f"{rtype} characteristic {c['name']}")
    expression = resource.get("expression") if isinstance(resource.get("expression"), dict) else None
    if expression and expression.get("expressionValue") is not None:  # a TMF921 intent expressed in RDF
        value = expression["expressionValue"]
        if isinstance(value, (dict, list)):
            lift_rdf(collector, json.dumps(value), "application/ld+json", kb_id, f"{subject}#expression")
        else:
            media = "application/ld+json" if "json" in str(expression.get("@type", "")).lower() else "text/turtle"
            lift_rdf(collector, str(value), media, kb_id, f"{subject}#expression")
    return subject, class_iri if typed else None


# ---- LLM extraction --------------------------------------------------------------------------------------------
async def ask(client, messages: list[dict]) -> dict:
    for attempt in (1, 2):
        try:
            content = await client.chat(messages, response_format={"type": "json_object"}, temperature=0)
        except ModelUnavailable as exc:
            raise StepError(f"model unavailable: {exc}") from exc
        except ModelRefused as exc:
            raise StepError(f"model refused the request: {exc}", retryable=False) from exc
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
        messages = messages + [{"role": "assistant", "content": content},
                               {"role": "user", "content": "That was not one valid JSON object. Answer again with "
                                                           "only the JSON object."}]
    raise StepError("the model did not answer with valid JSON twice")


def apply_extraction(collector: Collector, vocab: Vocabulary, kb_id: str, answer: dict, chunk_id: str) -> None:
    ids: dict[str, str] = {}
    for e in answer.get("entities") or []:
        if not isinstance(e, dict) or not e.get("id") or not e.get("label"):
            continue
        cls = vocab.resolve(str(e.get("type", "")), "class")
        if cls is None:
            collector.propose(str(e.get("type")), "class", f"{e.get('label')} in {chunk_id}")
            continue
        iri = entity_iri(kb_id, cls, str(e["label"]))
        ids[str(e["id"])] = iri
        collector.add(iri, RDF_TYPE, cls, "iri")
        collector.add(iri, RDFS_LABEL, str(e["label"]), "literal")
        collector.entity(iri, cls, str(e["label"]), chunk_id)
    for r in answer.get("relations") or []:
        if not isinstance(r, dict) or str(r.get("subject")) not in ids:
            continue
        predicate = vocab.resolve(str(r.get("predicate", "")), "property")
        obj = ids.get(str(r.get("object"))) or (str(r.get("object")) if str(r.get("object")) in vocab.terms else None)
        if obj is None:
            continue
        if predicate is None:
            collector.propose(str(r.get("predicate")), "predicate", f"{r.get('subject')} -> {r.get('object')}")
            continue
        collector.add(ids[str(r["subject"])], predicate, obj, "iri")
    for a in answer.get("attributes") or []:
        if not isinstance(a, dict) or str(a.get("subject")) not in ids or a.get("value") in (None, ""):
            continue
        predicate = vocab.resolve(str(a.get("predicate", "")), "property")
        if predicate is None:
            collector.propose(str(a.get("predicate")), "predicate", f"{a.get('subject')}: {a.get('value')}")
            continue
        collector.add(ids[str(a["subject"])], predicate, str(a["value"]), "literal", a.get("datatype"))


def handler(settings=None):
    max_terms = getattr(settings, "extraction_max_terms", 150)
    cache = VocabularyCache()

    async def km_fx_02(task: dict, client) -> dict:
        previous = task.get("input") or {}
        kb_id = task["payload"]["kbId"]
        vocab = await cache.get(client, kb_id)
        collector = Collector(vocab)
        kind = previous.get("kind")
        chunks = [dict(c) for c in previous.get("chunks") or []]
        if kind == "rdf":
            lift_rdf(collector, previous["content"], previous["mediaType"], kb_id, previous.get("documentId", ""))
        elif kind == "tmf":
            by_chunk = {}
            for i, record in enumerate(previous.get("records") or []):
                subject, cls = lift_tmf(collector, record, kb_id)
                by_chunk[f"{record['source']}:{record['resource'].get('id') or i}"] = (subject, cls)
            for c in chunks:
                subject, cls = by_chunk.get(c["chunkId"], (None, None))
                c["entityIri"], c["classIri"] = ([subject] if subject else []), ([cls] if cls else [])
        elif kind == "text":
            for c in chunks if vocab.classes else []:  # no ontology classes: nothing can be extracted
                terms = vocab.select(c["text"], max_terms)
                answer = await ask(client, [{"role": "system", "content": SYSTEM + Vocabulary.render(terms)},
                                            {"role": "user", "content": c["text"]}])
                apply_extraction(collector, vocab, kb_id, answer, c["chunkId"])
            for c in chunks:
                found = [e for e in collector.entities.values() if c["chunkId"] in e["chunkIds"]]
                c["entityIri"] = [e["iri"] for e in found]
                c["classIri"] = sorted({e["type"] for e in found})
        elif not kind:
            raise StepError("KM-FX-02 got no input from KM-FX-01", retryable=False)
        else:
            raise StepError(f"KM-FX-02 cannot extract from {kind!r}", retryable=False)
        out = {"kind": kind, "chunks": chunks, **collector.result()}
        for key in ("documentId", "source", "mediaType"):
            if previous.get(key):
                out[key] = previous[key]
        return out

    return km_fx_02
