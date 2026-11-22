"""knowledgeSpecification CRUD, content, imports, events and RBAC through the API."""
import pytest

from .api import auth, running_api

pytestmark = pytest.mark.integration

CORE = {"@type": "KnowledgeSpecification", "id": "core", "name": "Core", "iri": "https://example.org/core",
        "prefix": "core", "layer": "Foundational", "specKind": "ontology", "source": {"kind": "upload"}}
ASSURANCE_TTL = b"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://example.org/assurance> a owl:Ontology ; owl:imports <https://example.org/core>, <https://example.org/missing> .
"""


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), mount_roots=[str(tmp_path)]) as client:
        yield client


def path(api, *parts):
    return "/".join([api.base, "knowledgeSpecification", *parts])


async def test_create_get_and_list(api):
    created = await api.post(path(api), json=CORE, headers=auth("ontology-editor"))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"] == "core" and body["lifecycleStatus"] == "draft"
    assert body["href"] == f"https://km.example.org{api.base}/knowledgeSpecification/core"
    got = await api.get(path(api, "core"), headers=auth("knowledge-viewer"))
    assert got.json()["iri"] == CORE["iri"]
    listed = await api.get(path(api), params={"layer": "Foundational", "fields": "name"},
                           headers=auth("ontology-reviewer"))
    assert listed.headers["x-total-count"] == "1"
    assert listed.json() == [{"@type": "KnowledgeSpecification", "id": "core", "href": body["href"], "name": "Core"}]
    assert (await api.get(path(api), params={"filter": "$[?(@.x)]"}, headers=auth())).status_code == 501


async def test_ids_are_validated_derived_and_unique(api):
    editor = auth("ontology-editor")
    assert (await api.post(path(api), json={**CORE, "id": "Bad_Id"}, headers=editor)).status_code == 400
    first = (await api.post(path(api), json={k: v for k, v in CORE.items() if k != "id"}, headers=editor)).json()
    assert first["id"] == "core"
    assert (await api.post(path(api), json={**CORE, "iri": "https://example.org/other"},
                           headers=editor)).status_code == 409
    second = await api.post(path(api), json={**{k: v for k, v in CORE.items() if k != "id"},
                                             "iri": "https://example.org/core2"}, headers=editor)
    assert second.json()["id"].startswith("core-")
    assert (await api.post(path(api), json={**CORE, "id": "dup"}, headers=editor)).status_code == 409  # same iri


async def test_rbac(api):
    assert (await api.post(path(api), json=CORE, headers=auth("knowledge-viewer"))).status_code == 403
    assert (await api.post(path(api), json=CORE, headers=auth("ontology-approver"))).status_code == 403
    assert (await api.get(path(api))).status_code == 401
    await api.post(path(api), json=CORE, headers=auth("ontology-editor"))
    patch = await api.patch(path(api, "core"), json={"description": "x"}, headers=auth("ontology-reviewer"))
    assert patch.status_code == 403
    # lifecycle transitions go through the governance workflow (#28)
    assert (await api.patch(path(api, "core"), json={"lifecycleStatus": "proposed"},
                            headers=auth("ontology-editor"))).status_code == 200


async def test_content_upload_resolves_imports_and_emits_events(api):
    editor = auth("ontology-editor")
    await api.post(path(api), json=CORE, headers=editor)
    await api.post(path(api), json={**CORE, "id": "assurance", "name": "Assurance", "prefix": "as",
                                    "iri": "https://example.org/assurance", "layer": "Telecom"}, headers=editor)
    put = await api.put(path(api, "assurance", "content"), content=ASSURANCE_TTL,
                        headers={**editor, "Content-Type": "text/turtle"})
    assert put.status_code == 204, put.text
    body = (await api.get(path(api, "assurance"), headers=editor)).json()
    assert [r["id"] for r in body["imports"]] == ["core"]  # the missing import is skipped
    content = await api.get(path(api, "assurance", "content"), headers=auth())
    assert b"owl:imports" in content.content or b"imports" in content.content
    types = api.app.state.events.types()
    assert types.count("KnowledgeSpecificationCreateEvent") == 2
    assert "KnowledgeSpecificationAttributeValueChangeEvent" in types


async def test_contribution_content_needs_the_contributing_component(api):
    await api.post(path(api), json={**CORE, "source": {"kind": "componentContribution",
                                                       "componentName": "pc-productcatalogmanagement"}},
                   headers=auth("ontology-editor"))
    turtle = {"Content-Type": "text/turtle"}
    ttl = b"<urn:a> <urn:b> <urn:c> ."
    other = auth("canvasRole", extra={"azp": "someone-else"})
    assert (await api.put(path(api, "core", "content"), content=ttl, headers={**other, **turtle})).status_code == 403
    mine = auth("canvasRole", extra={"azp": "pc-productcatalogmanagement"})
    assert (await api.put(path(api, "core", "content"), content=ttl, headers={**mine, **turtle})).status_code == 204
    editor = auth("ontology-editor")
    assert (await api.put(path(api, "core", "content"), content=ttl, headers={**editor, **turtle})).status_code == 403


async def test_mount_source_loads_content(api, tmp_path):
    (tmp_path / "m.ttl").write_text("<urn:a> <urn:b> <urn:c> .")
    created = await api.post(path(api), json={**CORE, "source": {"kind": "mount", "path": str(tmp_path / "m.ttl")}},
                             headers=auth("ontology-editor"))
    assert created.status_code == 201
    assert b"urn:a" in (await api.get(path(api, "core", "content"), headers=auth())).content
    bad = await api.post(path(api), json={**CORE, "id": "x", "iri": "https://example.org/x",
                                          "source": {"kind": "mount", "path": "/etc/hostname"}},
                         headers=auth("ontology-editor"))
    assert bad.status_code == 400


async def test_delete_rules(api):
    editor, approver = auth("ontology-editor"), auth("ontology-approver")
    await api.post(path(api), json=CORE, headers=editor)
    await api.post(path(api), json={**CORE, "id": "child", "iri": "https://example.org/child",
                                    "imports": [{"id": "core"}]}, headers=editor)
    assert (await api.delete(path(api, "core"), headers=editor)).status_code == 409  # imported by child
    assert (await api.delete(path(api, "child"), headers=editor)).status_code == 204
    assert (await api.delete(path(api, "core"), headers=approver)).status_code == 204
    assert (await api.get(path(api, "core"), headers=editor)).status_code == 404
    assert "KnowledgeSpecificationDeleteEvent" in api.app.state.events.types()


async def test_schema_endpoint(api):
    response = await api.get(f"{api.base}/schema/FederatedKnowledgeSpecification.schema.json", headers=auth())
    assert response.status_code == 200 and response.json()["title"] == "FederatedKnowledgeSpecification"
    assert (await api.get(f"{api.base}/schema/../config.py", headers=auth())).status_code == 404
