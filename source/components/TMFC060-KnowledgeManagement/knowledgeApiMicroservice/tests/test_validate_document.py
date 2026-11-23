"""SHACL validation of a submitted document against a shapes module (the MCP validate tool's REST call)."""
import pytest

from .api import auth, running_api

pytestmark = pytest.mark.integration

ED, REV, APP = "ontology-editor", "ontology-reviewer", "ontology-approver"
AS = "https://example.org/as#"
SHAPES = f"""@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix as: <{AS}> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://example.org/as-shapes> a owl:Ontology .
as:AlarmShape a sh:NodeShape ; sh:targetClass as:Alarm ;
  sh:property [ sh:path as:severity ; sh:minCount 1 ; sh:in ( "minor" "major" "critical" ) ] ."""


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store")) as client:
        base = f"{client.base}/knowledgeSpecification"
        for mid, kind, ttl in [("as-shapes", "shapes", SHAPES),
                               ("as", "ontology", f"<{AS}Alarm> a <http://www.w3.org/2002/07/owl#Class> .")]:
            await client.post(base, json={"id": mid, "name": mid, "iri": f"https://example.org/{mid}", "specKind": kind,
                                          "source": {"kind": "upload"}}, headers=auth(ED))
            await client.put(f"{base}/{mid}/content", content=ttl.encode(),
                             headers={**auth(ED), "Content-Type": "text/turtle"})
        yield client


async def validate(api, body, module="as-shapes", media="text/turtle"):
    return await api.post(f"{api.base}/knowledgeSpecification/{module}/validation/document", content=body.encode(),
                          headers={**auth("knowledge-viewer"), "Content-Type": media})


async def test_conforming_and_violating_documents(api):
    ok = await validate(api, f'<urn:a1> a <{AS}Alarm> ; <{AS}severity> "major" .')
    assert ok.status_code == 200 and ok.json()["conforms"] is True and ok.json()["results"] == []
    assert ok.json()["shapesModule"] == "as-shapes"
    bad = await validate(api, f'<urn:a1> a <{AS}Alarm> ; <{AS}severity> "apocalyptic" . <urn:a2> a <{AS}Alarm> .')
    body = bad.json()
    assert body["conforms"] is False and {r["focusNode"] for r in body["results"]} == {"urn:a1", "urn:a2"}
    jsonld = await validate(api, '{"@id": "urn:a3", "@type": "https://example.org/as#Alarm"}', media="application/ld+json")
    assert jsonld.json()["conforms"] is False


async def test_errors(api):
    assert (await validate(api, "<urn:a> <urn:b> <urn:c> .", module="as")).status_code == 409  # not a shapes module
    assert (await validate(api, "x", media="application/rdf+xml")).status_code == 415
    assert (await validate(api, "not turtle <")).status_code == 400
    assert (await validate(api, "<urn:a> <urn:b> <urn:c> .", module="nope")).status_code == 404
