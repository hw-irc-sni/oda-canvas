"""The ontology vocabulary of a knowledge-base (classes and properties of its conformsTo closure), read with the
internal SPARQL call and cached per worker."""
import re
import time
from dataclasses import dataclass, field

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
# predicates always allowed, whatever the ontology
BASE_PREDICATES = {RDF + "type", RDFS + "label", RDFS + "comment", SKOS + "prefLabel", SKOS + "altLabel",
                   SKOS + "definition", RDF + "first", RDF + "rest"}  # RDF list structure is not vocabulary
# schema in instance data is kept with it: these predicates and these types (for the terms the data declares)
SCHEMA_PREDICATES = {RDFS + "subClassOf", RDFS + "subPropertyOf", RDFS + "domain", RDFS + "range"}
KINDS = {OWL + "Class": "class", RDFS + "Class": "class", OWL + "ObjectProperty": "object",
         OWL + "DatatypeProperty": "datatype", RDF + "Property": "property", OWL + "AnnotationProperty": "annotation"}
MODULES = 'FILTER(STRSTARTS(STR(?g), "urn:tmfc060:module:"))'
QUERY = f"""SELECT ?t ?type ?label ?comment ?domain ?range ?super WHERE {{
  VALUES ?type {{ {' '.join(f'<{k}>' for k in KINDS)} }}
  GRAPH ?g {{ ?t a ?type }} {MODULES} FILTER(isIRI(?t))
  OPTIONAL {{ ?t <{RDFS}label> ?label }} OPTIONAL {{ ?t <{RDFS}comment> ?comment }}
  OPTIONAL {{ ?t <{RDFS}domain> ?domain FILTER(isIRI(?domain)) }}
  OPTIONAL {{ ?t <{RDFS}range> ?range FILTER(isIRI(?range)) }}
  OPTIONAL {{ ?t <{RDFS}subClassOf> ?super FILTER(isIRI(?super)) }}
}}"""
# every other term the ontology modules declare with some type (TIO's fun:Function terms, …) and every superclass
DECLARED = f"""SELECT DISTINCT ?t ?role WHERE {{
  {{ GRAPH ?g {{ ?t a ?x }} {MODULES} BIND("term" AS ?role) }}
  UNION {{ GRAPH ?g {{ ?s <{RDFS}subClassOf> ?t }} {MODULES} BIND("class" AS ?role) }}
  FILTER(isIRI(?t))
}}"""
CACHE_SECONDS = 300


def local_name(iri: str) -> str:
    return re.split(r"[#/:]", iri.rstrip("#/"))[-1]


def words(text: str) -> set[str]:
    out = set()
    for word in re.findall(r"[^\W_]+", text):
        out.update(p.lower() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+|[^\W\d_]+", word) or [word])
        out.add(word.lower())
    return out


@dataclass
class Term:
    iri: str
    kind: str
    label: str = ""
    comment: str = ""
    domain: set = field(default_factory=set)
    range: set = field(default_factory=set)
    supers: set = field(default_factory=set)


class Vocabulary:
    def __init__(self, terms: dict[str, Term], declared: set[str] | None = None, superclasses: set[str] | None = None):
        self.terms = terms
        self.classes = {i for i, t in terms.items() if t.kind == "class"} | (superclasses or set())
        self.properties = {i for i, t in terms.items() if t.kind != "class"}
        # any term the ontology declares may be used as a predicate (it is not offered to the model, though)
        self.declared = (declared or set()) | self.properties
        self.by_local: dict[str, list[str]] = {}
        for iri in terms:
            self.by_local.setdefault(local_name(iri).lower(), []).append(iri)

    @classmethod
    def from_results(cls, results: dict, declared: dict | None = None) -> "Vocabulary":
        terms: dict[str, Term] = {}
        for b in results["results"]["bindings"]:
            iri = b["t"]["value"]
            kind = KINDS[b["type"]["value"]]
            term = terms.get(iri)
            if term is None or (term.kind in ("property", "annotation") and kind in ("object", "datatype")):
                term = terms[iri] = Term(iri, kind, **({} if term is None else {
                    "label": term.label, "comment": term.comment, "domain": term.domain, "range": term.range,
                    "supers": term.supers}))
            if "label" in b and not term.label:
                term.label = b["label"]["value"]
            if "comment" in b and not term.comment:
                term.comment = b["comment"]["value"]
            for key, attr in (("domain", "domain"), ("range", "range"), ("super", "supers")):
                if key in b:
                    getattr(term, attr).add(b[key]["value"])
        rows = (declared or {}).get("results", {}).get("bindings", [])
        return cls(terms, {b["t"]["value"] for b in rows if b["role"]["value"] == "term"},
                   {b["t"]["value"] for b in rows if b["role"]["value"] == "class"})

    def resolve(self, name: str, want: str) -> str | None:
        """An IRI or a unique local name -> a term of the wanted kind ('class' or 'property')."""
        name = (name or "").strip().strip("<>")
        pool = self.classes if want == "class" else self.properties | BASE_PREDICATES
        if name in pool:
            return name
        candidates = [i for i in self.by_local.get(local_name(name).lower(), []) if i in pool]
        if len(candidates) == 1:
            return candidates[0]
        if want == "property":
            base = [p for p in BASE_PREDICATES if local_name(p).lower() == local_name(name).lower()]
            if len(base) == 1 and name.lower() in {local_name(base[0]).lower(), base[0].lower()}:
                return base[0]
        return None

    def select(self, text: str, limit: int) -> list[Term]:
        """All terms, or the `limit` best-matching the text (by shared words) plus their superclasses."""
        if len(self.terms) <= limit:
            return list(self.terms.values())
        bag = words(text)
        scored = sorted(self.terms.values(), key=lambda t: (-len(bag & words(f"{local_name(t.iri)} {t.label}")),
                                                            t.iri))
        chosen = {t.iri for t in scored[:limit]}
        for iri in list(chosen):
            chosen |= {s for s in self.terms[iri].supers if s in self.terms}
        return [self.terms[i] for i in sorted(chosen)]

    @staticmethod
    def render(terms: list[Term]) -> str:
        lines = []
        for t in sorted(terms, key=lambda t: (t.kind != "class", t.iri)):
            text = f"{'CLASS' if t.kind == 'class' else 'PROPERTY'} <{t.iri}>"
            if t.label:
                text += f' "{t.label}"'
            if t.kind != "class" and (t.domain or t.range):
                text += f" ({', '.join(sorted(t.domain)) or '*'} -> {', '.join(sorted(t.range)) or '*'})"
            if t.kind == "datatype":
                text += " [value]"
            if t.comment:
                text += f" — {t.comment[:100]}"
            lines.append(text)
        return "\n".join(lines)


class VocabularyCache:
    def __init__(self):
        self.entries: dict[str, tuple[float, Vocabulary]] = {}

    async def get(self, client, kb_id: str) -> Vocabulary:
        hit = self.entries.get(kb_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        vocab = Vocabulary.from_results(await client.query(kb_id, QUERY), await client.query(kb_id, DECLARED))
        self.entries[kb_id] = (time.monotonic() + CACHE_SECONDS, vocab)
        return vocab
