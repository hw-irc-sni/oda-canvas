"""Validation: the five rules (one negative fixture each), SHACL, the report, routes and RBAC."""
import pytest
from rdflib import Graph

from app.ontology.checks import check_module, repository_pass, shacl_validate

from .api import auth, running_api

PFX = """@prefix fto: <https://models.tmforum.org/ontology/fto/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . @prefix ex: <https://example.org/> .
"""
FOUND = PFX + """<https://example.org/found> a owl:Ontology ; fto:layer fto:Foundational .
ex:Event a owl:Class ; fto:layer fto:Foundational ."""
TELE = PFX + """<https://example.org/tele> a owl:Ontology ; fto:layer fto:Telecom .
ex:Alarm a owl:Class ; fto:layer fto:Telecom ; rdfs:subClassOf ex:Event ."""
IMPL = PFX + """ex:Probe a owl:Class ; fto:layer fto:Implementation ."""


def g(ttl):
    return Graph().parse(data=ttl, format="turtle")


def body(iri, layer="Telecom", kind="ontology"):
    return {"iri": iri, "layer": layer, "specKind": kind}


def union(*ttls):
    out = Graph()
    for t in ttls:
        out += g(t)
    return out


def messages(results, rule):
    return results.messages[rule]


IRIS = ["https://example.org/"]


def test_a_clean_module_passes():
    r = check_module(body("https://example.org/tele"), g(TELE), union(FOUND, TELE), IRIS, [])
    assert r.passed and not any(r.messages.values())


def test_rule1_no_content_is_a_warning():
    r = check_module(body("https://example.org/x"), None, union(FOUND), IRIS, [])
    assert r.passed and messages(r, "parse")[0]["severity"] == "warning"


def test_rule2_layer_mismatch_is_an_error_and_missing_layer_a_warning():
    r = check_module(body("https://example.org/tele", layer="Implementation"), g(TELE), union(FOUND, TELE), IRIS, [])
    assert not r.passed and "layer Implementation but fto:layer Telecom" in messages(r, "layerAnnotation")[0]["text"]
    no_layer = PFX + "<https://example.org/n> a owl:Ontology ."
    r = check_module(body("https://example.org/n"), g(no_layer), union(no_layer), IRIS, [])
    assert r.passed and messages(r, "layerAnnotation")[0]["severity"] == "warning"


def test_rule2b_class_deeper_than_its_module():
    deeper = TELE + "\nex:Probe a owl:Class ; fto:layer fto:Implementation ."
    r = check_module(body("https://example.org/tele"), g(deeper), union(FOUND, deeper), IRIS, [])
    assert "Probe is tagged Implementation" in messages(r, "layerAnnotation")[0]["text"] and not r.passed


def test_rule3_deeper_layer_inheritance_error_and_bridge_warning():
    bad = PFX + """<https://example.org/found2> a owl:Ontology ; fto:layer fto:Foundational .
ex:Cause a owl:Class ; fto:layer fto:Foundational ; rdfs:subClassOf ex:Alarm ."""
    r = check_module(body("https://example.org/found2", layer="Foundational"), g(bad), union(FOUND, TELE, bad), IRIS, [])
    assert not r.passed and "Cause (Foundational) extends deeper-layer Alarm (Telecom)" in \
        messages(r, "layerMonotonicity")[0]["text"]
    r = check_module(body("https://example.org/found2", layer="Foundational", kind="bridge"), g(bad),
                     union(FOUND, TELE, bad), IRIS, [])
    assert r.passed and messages(r, "layerMonotonicity")[0]["severity"] == "warning"
    linked = bad + '\nex:Cause fto:linkStatus "provisional" .'
    r = check_module(body("https://example.org/found2", layer="Foundational"), g(linked), union(FOUND, TELE, linked),
                     IRIS, [])
    assert r.passed and messages(r, "layerMonotonicity")[0]["severity"] == "warning"


def test_rule4_unresolved_parent():
    orphan = TELE.replace("rdfs:subClassOf ex:Event", "rdfs:subClassOf ex:Missing")
    r = check_module(body("https://example.org/tele"), g(orphan), union(FOUND, orphan), IRIS, [])
    assert not r.passed and "ex" not in messages(r, "parentsResolve")[0]["text"][:0]
    assert "https://example.org/Missing" in messages(r, "parentsResolve")[0]["text"]
    outside = TELE.replace("rdfs:subClassOf ex:Event", "rdfs:subClassOf <http://elsewhere.example/Thing>")
    assert check_module(body("https://example.org/tele"), g(outside), union(FOUND, outside), IRIS, []).passed


def test_rule5_iri_not_declared_and_unresolved_import():
    r = check_module(body("https://example.org/other"), g(TELE), union(FOUND, TELE), IRIS, [])
    assert not r.passed and "does not declare" in messages(r, "catalogIntegrity")[0]["text"]
    r = check_module(body("https://example.org/tele"), g(TELE), union(FOUND, TELE), IRIS,
                     ["https://example.org/missing"])
    assert r.passed and messages(r, "catalogIntegrity")[0]["severity"] == "warning"


SHAPES = """@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix ex: <https://example.org/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:LabelShape a sh:NodeShape ; sh:targetClass ex:Alarm ;
  sh:property [ sh:path rdfs:label ; sh:minCount 1 ; sh:message "Every alarm needs a label" ] ."""


def test_shacl_validate():
    data = g(PFX + "ex:a1 a ex:Alarm . ex:a2 a ex:Alarm ; rdfs:label \"ok\" .")
    out = shacl_validate(data, g(SHAPES), g(TELE))
    assert not out["conforms"] and len(out["results"]) == 1
    result = out["results"][0]
    assert result["focusNode"] == "https://example.org/a1" and result["severity"] == "Violation"
    assert result["message"] == "Every alarm needs a label"
    assert shacl_validate(data, Graph()) == {"conforms": True, "results": []}


def test_repository_pass(tmp_path):
    (tmp_path / "a.ttl").write_text(TELE)
    (tmp_path / "bad.ttl").write_text("not turtle")
    (tmp_path / "_sources").mkdir()
    (tmp_path / "_sources" / "x.ttl").write_text("ignored garbage")
    (tmp_path / "catalog.json").write_text('{"ontologies": {"https://example.org/tele": "a.ttl", '
                                           '"https://example.org/gone": "gone.ttl"}}')
    out = repository_pass(tmp_path)
    assert out["filesParsed"] == 1
    assert any("bad.ttl" in e for e in out["errors"]) and any("gone.ttl missing" in e for e in out["errors"])


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store")) as client:
        yield client


@pytest.mark.integration
async def test_validation_routes(api):
    editor, base = auth("ontology-editor"), f"{api.base}/knowledgeSpecification"
    turtle = {**editor, "Content-Type": "text/turtle"}
    for mid, iri, layer, kind, ttl in [("found", "https://example.org/found", "Foundational", "ontology", FOUND),
                                       ("shapes", "https://example.org/shapes", "Foundational", "shapes", SHAPES),
                                       ("tele", "https://example.org/tele", "Telecom", "ontology", TELE)]:
        imports = [{"id": "found"}, {"id": "shapes"}] if mid == "tele" else []
        created = await api.post(base, json={"id": mid, "name": mid, "iri": iri, "layer": layer, "specKind": kind,
                                             "source": {"kind": "upload"}, "imports": imports}, headers=editor)
        assert created.status_code == 201, created.text
        assert (await api.put(f"{base}/{mid}/content", content=ttl.encode(), headers=turtle)).status_code == 204
    data = TELE + '\nex:a1 a ex:Alarm .'
    await api.put(f"{base}/tele/content", content=data.encode(), headers=turtle)
    assert (await api.get(f"{base}/tele/validation", headers=auth())).status_code == 404
    assert (await api.post(f"{base}/tele/validation", headers=auth("knowledge-viewer"))).status_code == 403
    run = await api.post(f"{base}/tele/validation", headers=auth("ontology-reviewer"))
    assert run.status_code == 200, run.text
    report = run.json()
    assert report["moduleId"] == "tele" and [r["rule"] for r in report["rules"]] == [
        "parse", "layerAnnotation", "layerMonotonicity", "parentsResolve", "catalogIntegrity"]
    assert all(r["passed"] for r in report["rules"])
    assert report["shacl"]["conforms"] is False and report["conforms"] is False
    last = await api.get(f"{base}/tele/validation", headers=auth("knowledge-viewer"))
    assert last.json() == report


def test_rdfs_class_counts_as_declared():
    rdfs_parent = PFX + "ex:Event a rdfs:Class ."
    r = check_module(body("https://example.org/tele"), g(TELE), union(rdfs_parent, TELE), IRIS, [])
    assert not r.messages["parentsResolve"]


def test_no_ontology_declared_is_a_warning_and_placeholders_are_not_checked():
    classes_only = PFX + "ex:Alarm a rdfs:Class ."
    r = check_module(body("https://example.org/tio"), g(classes_only), union(classes_only), IRIS, [])
    assert r.passed and r.messages["catalogIntegrity"][0]["text"] == "the content declares no owl:Ontology"
    r = check_module(body("urn:tmfc060:federation:fto:shapes"), g(classes_only), union(classes_only), IRIS, [])
    assert not r.messages["catalogIntegrity"]
