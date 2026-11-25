"""Proposed changes to a knowledge-base: recorded through the internal API, decided through the public one."""
import httpx
import pytest

from .api import auth, running_api

pytestmark = pytest.mark.integration

EDITOR, ONT_EDITOR, REVIEWER, APPROVER = "knowledge-editor", "ontology-editor", "ontology-reviewer", "ontology-approver"
TOKEN = {"Authorization": "Bearer t0ken"}
AS = "https://example.org/as#"


@pytest.fixture
async def api(postgres_url, tmp_path):
    token = tmp_path / "token"
    token.write_text("t0ken")
    async with running_api(postgres_url, str(tmp_path / "store"), internal_token_file=str(token),
                           governance_require_validation=False) as client:
        client.internal = httpx.AsyncClient(base_url=f"http://127.0.0.1:{client.app.state.internal.port}")
        await client.post(f"{client.base}/knowledge", json={"id": "kb", "name": "KB", "storeKind": "graph"},
                          headers=auth(EDITOR))
        yield client
        await client.internal.aclose()


CONFLICT = {"kind": "dataConflict", "summary": "Cell 4711 has two operators",
            "details": {"triples": [["urn:x:cell", AS + "operator", "ACME", "literal", None]],
                        "clash": {"property": AS + "operator", "existing": ["Globex"]}},
            "suggestion": {"action": "keepExisting", "rationale": "the inventory says Globex"}}
LINK = {"kind": "entityLink", "summary": "Cell 4711 matches two cells",
        "details": {"iri": "urn:x:new", "label": "Cell 4711", "type": AS + "Cell", "candidates": ["urn:x:a", "urn:x:b"]}}
TERM = {"kind": "ontologyTerm", "summary": "as:Battery is not in the ontology",
        "details": {"term": AS + "Battery", "role": "class", "count": 2}}


async def propose(api, items, source="doc-1"):
    r = await api.internal.post("/internal/knowledge/kb/proposal", json={"source": source, "processId": "pf-1",
                                                                         "proposals": items}, headers=TOKEN)
    assert r.status_code == 201, r.text
    return r.json()["ids"]


async def ask(api, query):
    r = await api.post(f"{api.base}/knowledge/kb/query", content=query.encode(),
                       headers={**auth(), "Content-Type": "application/sparql-query"})
    return r.json()["boolean"]


async def test_record_list_and_decide(api):
    conflict, link, term = await propose(api, [CONFLICT, LINK, TERM])
    base = f"{api.base}/knowledge/kb/proposal"
    listed = (await api.get(base, headers=auth("knowledge-viewer"))).json()
    assert [p["kind"] for p in listed] == ["dataConflict", "entityLink", "ontologyTerm"]
    assert listed[0]["state"] == "proposed" and listed[0]["suggestion"]["action"] == "keepExisting"
    assert listed[0]["source"] == "doc-1" and listed[0]["processId"] == "pf-1"
    assert not await ask(api, f'ASK {{ <urn:x:cell> <{AS}operator> "ACME" }}')  # withheld
    r = await api.patch(f"{base}/{conflict}", json={"state": "accepted"}, headers=auth(EDITOR))
    assert r.status_code == 200 and r.json()["state"] == "accepted" and r.json()["decidedBy"] == "alice"
    assert await ask(api, f'ASK {{ <urn:x:cell> <{AS}operator> "ACME" }}')  # accepted: now written
    assert (await api.patch(f"{base}/{conflict}", json={"state": "rejected"},
                            headers=auth(EDITOR))).status_code == 409  # already decided
    assert (await api.patch(f"{base}/{link}", json={"state": "accepted", "choice": "urn:x:z"},
                            headers=auth(EDITOR))).status_code == 400
    r = await api.patch(f"{base}/{link}", json={"state": "accepted", "choice": "urn:x:b"}, headers=auth(EDITOR))
    assert r.json()["details"]["choice"] == "urn:x:b"
    assert await ask(api, "ASK { <urn:x:new> <http://www.w3.org/2002/07/owl#sameAs> <urn:x:b> }")
    assert (await api.patch(f"{base}/{term}", json={"state": "accepted"}, headers=auth(EDITOR))).status_code == 409
    assert (await api.patch(f"{base}/{term}", json={"state": "maybe"}, headers=auth(EDITOR))).status_code == 400
    assert (await api.patch(f"{base}/{term}", json={"state": "rejected"},
                            headers=auth("knowledge-viewer"))).status_code == 403
    assert (await api.get(f"{base}/kp-nope", headers=auth())).status_code == 404


async def test_reingest_replaces_undecided_proposals(api):
    first = await propose(api, [CONFLICT, TERM])
    await api.patch(f"{api.base}/knowledge/kb/proposal/{first[0]}", json={"state": "rejected"}, headers=auth(EDITOR))
    await propose(api, [TERM])  # the same source again
    kinds = [(p["kind"], p["state"]) for p in (await api.get(f"{api.base}/knowledge/kb/proposal",
                                                             headers=auth())).json()]
    assert kinds == [("dataConflict", "rejected"), ("ontologyTerm", "proposed")]  # decided ones are kept
    assert (await api.internal.post("/internal/knowledge/kb/proposal", json={"proposals": [{"kind": "x"}]},
                                    headers=TOKEN)).status_code == 400
    assert (await api.internal.post("/internal/knowledge/kb/proposal", json={"proposals": []})).status_code == 401


async def test_internal_ontology_and_shapes(api):
    base = f"{api.base}/knowledgeSpecification"
    for mid, kind, ttl in [("as", "ontology", f"<{AS}Cell> a <http://www.w3.org/2002/07/owl#Class> ."),
                           ("as-shapes", "shapes", f"<{AS}CellShape> a <http://www.w3.org/ns/shacl#NodeShape> .")]:
        await api.post(base, json={"id": mid, "name": mid, "iri": f"https://example.org/{mid}", "specKind": kind,
                                   "source": {"kind": "upload"}}, headers=auth(ONT_EDITOR))
        await api.put(f"{base}/{mid}/content", content=ttl.encode(),
                      headers={**auth(ONT_EDITOR), "Content-Type": "text/turtle"})
        for s, role in [("proposed", ONT_EDITOR), ("inReview", REVIEWER), ("agreed", APPROVER),
                        ("published", APPROVER)]:
            await api.patch(f"{base}/{mid}", json={"lifecycleStatus": s}, headers=auth(role))
    await api.patch(f"{api.base}/knowledge/kb", json={"conformsTo": [{"id": "as"}, {"id": "as-shapes"}]},
                    headers=auth(EDITOR))
    onto = await api.internal.get("/internal/knowledge/kb/ontology", headers=TOKEN)
    shapes = await api.internal.get("/internal/knowledge/kb/ontology", params={"part": "shapes"}, headers=TOKEN)
    assert b"Cell>" in onto.content and b"CellShape" not in onto.content
    assert b"CellShape" in shapes.content and b"owl#Class" not in shapes.content
    assert (await api.internal.get("/internal/knowledge/kb/ontology", params={"part": "x"},
                                   headers=TOKEN)).status_code == 400
