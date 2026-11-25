"""Validation of ontology modules: the five structural rules of FTO's scripts/check.py, and SHACL (pyshacl, as
FTO's scripts/validate.py).

Rules (a module passes when no rule reports an error):

1. ``parse``: the module's content parses. A module without content gets a warning.
2. ``layerAnnotation``: the ``fto:layer`` of the module's ontology equals its ``layer`` (error); a missing
   annotation is a warning. No class may be tagged with a deeper layer than its module (error).
3. ``layerMonotonicity``: no class of the module may extend a class of a deeper layer (error). In a bridge-like
   module (``specKind: bridge``, or content using ``fto:linkStatus``) it is a warning, as check.py treats its
   bridge files.
4. ``parentsResolve``: every ``rdfs:subClassOf`` parent in the namespace of a registered module with content is
   declared as a class (``owl:Class`` or ``rdfs:Class``).
5. ``catalogIntegrity``: the content declares the module's IRI as an ``owl:Ontology`` (error if it declares
   another one, warning if it declares none; not checked for placeholder IRIs); ``owl:imports`` that resolve to
   no registered module are warnings.

Like check.py, which merges all its modules, the class and layer lookups of rules 3 and 4 use the union of every
registered module's content.

SHACL: a module's content, with its import closure as the ontology graph, is validated against the ``shapes``
modules it imports.
"""
from datetime import datetime, timezone

from pyshacl import validate as pyshacl_validate
from rdflib import OWL, RDF, RDFS, Graph, Namespace, URIRef

FTO = Namespace("https://models.tmforum.org/ontology/fto/")
SH = Namespace("http://www.w3.org/ns/shacl#")
RANK = {FTO.Foundational: 0, FTO.Telecom: 1, FTO.Implementation: 2}
LAYER_IRI = {"Foundational": FTO.Foundational, "Telecom": FTO.Telecom, "Implementation": FTO.Implementation}
RULES = ("parse", "layerAnnotation", "layerMonotonicity", "parentsResolve", "catalogIntegrity")


def short(iri) -> str:
    return str(iri).rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


class RuleResults:
    def __init__(self):
        self.messages = {rule: [] for rule in RULES}

    def error(self, rule, text):
        self.messages[rule].append({"severity": "error", "text": text})

    def warning(self, rule, text):
        self.messages[rule].append({"severity": "warning", "text": text})

    def as_list(self):
        return [{"rule": rule, "passed": not any(m["severity"] == "error" for m in msgs),
                 **({"messages": msgs} if msgs else {})} for rule, msgs in self.messages.items()]

    @property
    def passed(self):
        return all(r["passed"] for r in self.as_list())


def check_module(body: dict, content: Graph | None, union: Graph, registered_iris: list[str],
                 unresolved_imports: list[str]) -> RuleResults:
    """The five rules for one module. `union` holds the content of every registered module."""
    results = RuleResults()
    if content is None or len(content) == 0:
        results.warning("parse", "the module has no content")
        content = Graph()
    module_iri = URIRef(body["iri"])
    ontologies = set(content.subjects(RDF.type, OWL.Ontology))

    # 2. layer annotation
    layers = [o for ont in ontologies for o in content.objects(ont, FTO.layer)]
    expected = LAYER_IRI.get(body.get("layer"))
    if len(content):
        if not layers:
            results.warning("layerAnnotation", "the ontology has no fto:layer")
        elif expected is not None and layers[0] != expected:
            results.error("layerAnnotation", f"layer {body.get('layer')} but fto:layer {short(layers[0])}")
    module_rank = RANK.get(layers[0] if layers else expected, None)
    if module_rank is not None:
        for cls in set(content.subjects(RDF.type, OWL.Class)) | set(content.subjects(RDF.type, RDFS.Class)):
            for tagged in content.objects(cls, FTO.layer):
                if RANK.get(tagged, 0) > module_rank:
                    results.error("layerAnnotation", f"class {short(cls)} is tagged {short(tagged)}, deeper than "
                                                     f"its module")

    # 3. layer monotonicity and 4. parents resolve, on this module's subClassOf statements
    bridge_like = body.get("specKind") == "bridge" or any(True for _ in content.triples((None, FTO.linkStatus, None)))
    declared = set(union.subjects(RDF.type, OWL.Class)) | set(union.subjects(RDF.type, RDFS.Class))

    def layer_of(cls):
        found = list(union.objects(cls, FTO.layer))
        return found[0] if found else None

    for child, parent in content.subject_objects(RDFS.subClassOf):
        if not isinstance(parent, URIRef):
            continue
        if parent not in declared and any(str(parent).startswith(iri) for iri in registered_iris):
            results.error("parentsResolve", f"parent {parent} of {child} is not declared")
        lc, lp = layer_of(child), layer_of(parent)
        if lc in RANK and lp in RANK and RANK[lp] > RANK[lc]:
            text = f"{short(child)} ({short(lc)}) extends deeper-layer {short(parent)} ({short(lp)})"
            if bridge_like:
                results.warning("layerMonotonicity", text + " - asserted by a bridge; to be ruled on")
            else:
                results.error("layerMonotonicity", text)

    # 5. catalog integrity (no check for placeholder IRIs: by definition no content declares them)
    if len(content) and not str(module_iri).startswith("urn:tmfc060:"):
        if not ontologies:
            results.warning("catalogIntegrity", "the content declares no owl:Ontology")
        elif module_iri not in ontologies:
            results.error("catalogIntegrity", f"the content does not declare <{module_iri}> as an owl:Ontology")
    for iri in unresolved_imports:
        results.warning("catalogIntegrity", f"owl:imports <{iri}> resolves to no registered module")
    return results


def shacl_validate(data: Graph, shapes: Graph, ontology: Graph | None = None) -> dict:
    """SHACL-validate `data` against `shapes` (inference none, like FTO's validate.py). Returns the OAS form."""
    if len(shapes) == 0:
        return {"conforms": True, "results": []}
    conforms, report, _ = pyshacl_validate(data, shacl_graph=shapes, ont_graph=ontology, inference="none",
                                           abort_on_first=False)
    results = []
    for result in set(report.subjects(RDF.type, SH.ValidationResult)):
        entry = {"focusNode": str(report.value(result, SH.focusNode) or ""),
                 "resultPath": str(report.value(result, SH.resultPath) or ""),
                 "severity": short(report.value(result, SH.resultSeverity) or ""),
                 "message": str(report.value(result, SH.resultMessage) or ""),
                 "sourceShape": str(report.value(result, SH.sourceShape) or "")}
        results.append({k: v for k, v in entry.items() if v})
    results.sort(key=lambda r: (r.get("focusNode", ""), r.get("resultPath", ""), r.get("message", "")))
    return {"conforms": bool(conforms), "results": results}


def report(module_id: str, rules: RuleResults, shacl: dict) -> dict:
    return {"moduleId": module_id, "runAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "conforms": rules.passed and shacl["conforms"], "rules": rules.as_list(), "shacl": shacl}


def repository_pass(root) -> dict:
    """The repo-level part of check.py: every .ttl (outside _sources/) parses, and catalog.json is intact."""
    import json
    parsed, errors = 0, []
    for path in sorted(root.rglob("*.ttl")):
        if "_sources" in path.parts:
            continue
        try:
            Graph().parse(path, format="turtle")
            parsed += 1
        except Exception as exc:  # noqa: BLE001 - every parse failure is reported
            errors.append(f"parse {path.relative_to(root)}: {exc}")
    catalog = root / "catalog.json"
    if catalog.is_file():
        for iri, rel in json.loads(catalog.read_text()).get("ontologies", {}).items():
            path = root / rel
            if not path.is_file():
                errors.append(f"catalog: {rel} missing")
                continue
            graph = Graph().parse(path, format="turtle")
            if URIRef(iri) not in set(graph.subjects(RDF.type, OWL.Ontology)):
                errors.append(f"catalog: {rel} does not declare <{iri}>")
    return {"filesParsed": parsed, "errors": errors}
