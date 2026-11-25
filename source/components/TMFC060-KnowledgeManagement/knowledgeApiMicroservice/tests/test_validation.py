"""Request bodies are validated against the OAS before the handlers run."""
from pathlib import Path

import pytest

from app.validation import load_body_validators

from .api import auth, running_api

OAS = Path(__file__).resolve().parent.parent / "api" / "TMFxxx-KnowledgeManagement-v5.0.0.oas.yaml"

ED = "ontology-editor"


# Rules the server enforces in code, stated in the OAS too (each body below gets 400 from the handler as well)
REFUSED = [
    ("createKnowledge", {"name": " ", "storeKind": "graph"}),
    ("createKnowledgeSpecification", {"name": "", "iri": "https://e.org/x", "specKind": "ontology",
                                      "source": {"kind": "upload"}}),
    ("createKnowledgeSpecification", {"name": "x", "iri": "0", "specKind": "ontology", "source": {"kind": "upload"}}),
    ("createKnowledgeSpecification", {"name": "x", "iri": "https://e.org/x", "specKind": "remote",
                                      "source": {"kind": "upload"}}),
    ("patchKnowledgeSpecification", {"source": {"kind": "git", "url": "", "path": ""}}),
    ("patchKnowledgeSpecification", {"lifecycleStatus": "proposed", "name": "x"}),
    ("patchKnowledge", {"lifecycleStatus": "proposed", "description": "x"}),
    ("patchKnowledgeSpecificationMapping", {"lifecycleStatus": "proposed", "rationale": "x"}),
    ("patchKnowledgeSpecificationMapping", {"lifecycleStatus": "proposed", "@type": None}),
    ("createKnowledgeSpecificationReview", {"kind": "ruling", "target": {"kind": "module", "id": "m"}}),
    ("searchKnowledge", {"query": " "}),
    ("recommendFromKnowledge", {}),
    ("recommendFromKnowledge", {"context": " "}),
    ("createHub", {"callback": "http://127.0.0.1/cb", "query": "kind=all"}),
    ("createKnowledge", {"name": "k", "relatedParty": [{"partyOrPartyRole": {"@type": "Nope", "id": "p"}}]}),
]


def test_rules_the_server_enforces_are_in_the_oas():
    rules = load_body_validators(OAS)
    for op, body in REFUSED:
        assert not rules[op].is_valid(body), (op, body)
    assert rules["createKnowledge"].is_valid({"name": "k", "relatedParty": [
        {"partyOrPartyRole": {"@type": "PartyRoleRef", "id": "p", "partyId": "x"}}]})


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store")) as client:
        yield client


@pytest.mark.integration
async def test_wrongly_typed_bodies_get_400_not_500(api):
    base = f"{api.base}/knowledgeSpecification"
    for body, where in [({"name": "x", "iri": "https://e.org/x", "specKind": "ontology", "prefix": ["a"],
                          "source": {"kind": "upload"}}, "prefix"),
                        ({"name": ["x"], "iri": "https://e.org/x", "specKind": "ontology",
                          "source": {"kind": "upload"}}, "name"),
                        ({"name": "x", "iri": "https://e.org/x", "specKind": "tree", "source": {"kind": "upload"}},
                         "specKind"),
                        ([1, 2], "(body)")]:
        r = await api.post(base, json=body, headers=auth(ED))
        assert r.status_code == 400, (body, r.text)
        assert f"at {where}" in r.json()["message"]
    r = await api.post(f"{api.base}/knowledge", content=b"{not json", headers={**auth("knowledge-editor"),
                                                                               "Content-Type": "application/json"})
    assert r.status_code == 400 and "Invalid JSON" in r.json()["message"]


@pytest.mark.integration
async def test_type_is_optional_in_requests_but_checked_when_given(api):
    base = f"{api.base}/knowledge"
    ok = await api.post(base, json={"id": "a", "name": "A", "storeKind": "graph"}, headers=auth("knowledge-editor"))
    assert ok.status_code == 201 and ok.json()["@type"] == "Knowledge"
    bad = await api.post(base, json={"id": "b", "name": "B", "storeKind": "graph", "@type": "Thing"},
                         headers=auth("knowledge-editor"))
    assert bad.status_code == 400 and "@type" in bad.json()["message"]


@pytest.mark.integration
async def test_roles_are_checked_before_the_body(api):
    r = await api.post(f"{api.base}/knowledgeSpecification", json={"name": ["x"]}, headers=auth("knowledge-viewer"))
    assert r.status_code == 403


@pytest.mark.integration
async def test_date_times_are_checked(api):
    base = f"{api.base}/knowledge"
    await api.post(base, json={"id": "a", "name": "A", "storeKind": "graph"}, headers=auth("knowledge-editor"))
    for bad in ["", "yesterday", "2026-13-01T00:00:00Z", "2026-01-01"]:
        r = await api.patch(f"{base}/a", json={"validFor": {"endDateTime": bad}}, headers=auth("knowledge-editor"))
        assert r.status_code == 400 and "validFor/endDateTime" in r.json()["message"], bad
    ok = await api.patch(f"{base}/a", json={"validFor": {"endDateTime": "2027-01-01T00:00:00Z"}},
                         headers=auth("knowledge-editor"))
    assert ok.status_code == 200


@pytest.mark.integration
async def test_nul_characters_are_refused(api):
    r = await api.post(f"{api.base}/knowledge", json={"id": "a", "name": "A\u0000", "storeKind": "graph"},
                       headers=auth("knowledge-editor"))
    assert r.status_code == 400 and "NUL" in r.json()["message"]


@pytest.mark.integration
async def test_nested_types_are_set_from_the_oas(api):
    r = await api.post(f"{api.base}/knowledge", json={"id": "a", "name": "A", "storeKind": "graph", "relatedParty": [
        {"role": "steward", "partyOrPartyRole": {"id": "noc", "partyId": "ops", "name": "NOC"}},
        {"role": "contributor", "partyOrPartyRole": {"id": "p1"}}]}, headers=auth("knowledge-editor"))
    assert r.status_code == 201, r.text
    parties = {p["role"]: p for p in (await api.get(f"{api.base}/knowledge/a", headers=auth())).json()["relatedParty"]}
    assert parties["steward"]["@type"] == "RelatedPartyRefOrPartyRoleRef"
    assert parties["steward"]["partyOrPartyRole"]["@type"] == "PartyRoleRef"  # it has partyId
    assert parties["contributor"]["partyOrPartyRole"]["@type"] == "PartyRef"


@pytest.mark.integration
async def test_uris_are_checked(api):
    for bad in ["0", "", "not a uri"]:
        r = await api.post(f"{api.base}/knowledgeSpecification", json={
            "name": "x", "iri": bad, "specKind": "ontology", "source": {"kind": "upload"}}, headers=auth(ED))
        assert r.status_code == 400 and "at iri" in r.json()["message"], bad
