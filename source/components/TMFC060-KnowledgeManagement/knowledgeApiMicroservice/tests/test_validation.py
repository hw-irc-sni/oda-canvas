"""Request bodies are validated against the OAS before the handlers run."""
import pytest

from .api import auth, running_api

pytestmark = pytest.mark.integration

ED = "ontology-editor"


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store")) as client:
        yield client


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


async def test_type_is_optional_in_requests_but_checked_when_given(api):
    base = f"{api.base}/knowledge"
    ok = await api.post(base, json={"id": "a", "name": "A", "storeKind": "graph"}, headers=auth("knowledge-editor"))
    assert ok.status_code == 201 and ok.json()["@type"] == "Knowledge"
    bad = await api.post(base, json={"id": "b", "name": "B", "storeKind": "graph", "@type": "Thing"},
                         headers=auth("knowledge-editor"))
    assert bad.status_code == 400 and "@type" in bad.json()["message"]


async def test_roles_are_checked_before_the_body(api):
    r = await api.post(f"{api.base}/knowledgeSpecification", json={"name": ["x"]}, headers=auth("knowledge-viewer"))
    assert r.status_code == 403


async def test_date_times_are_checked(api):
    base = f"{api.base}/knowledge"
    await api.post(base, json={"id": "a", "name": "A", "storeKind": "graph"}, headers=auth("knowledge-editor"))
    for bad in ["", "yesterday", "2026-13-01T00:00:00Z", "2026-01-01"]:
        r = await api.patch(f"{base}/a", json={"validFor": {"endDateTime": bad}}, headers=auth("knowledge-editor"))
        assert r.status_code == 400 and "validFor/endDateTime" in r.json()["message"], bad
    ok = await api.patch(f"{base}/a", json={"validFor": {"endDateTime": "2027-01-01T00:00:00Z"}},
                         headers=auth("knowledge-editor"))
    assert ok.status_code == 200
