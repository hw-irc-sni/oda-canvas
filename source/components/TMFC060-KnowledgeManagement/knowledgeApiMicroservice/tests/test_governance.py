"""The governed lifecycle of ontology modules: transitions, roles, review items, the validation gate, RDF write-back."""
import pytest
from sqlalchemy import select

from app.db.models import StateTransition
from app.ontology.governance import load_lifecycles
from app.config import Settings

from .api import auth, running_api

pytestmark = pytest.mark.integration

GOOD = b"""@prefix fto: <https://models.tmforum.org/ontology/fto/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://example.org/mod> a owl:Ontology ; fto:layer fto:Telecom ."""
BAD = b"""@prefix owl: <http://www.w3.org/2002/07/owl#> . <https://example.org/elsewhere> a owl:Ontology ."""
EDITOR, REVIEWER, APPROVER = "ontology-editor", "ontology-reviewer", "ontology-approver"


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), mount_roots=[str(tmp_path)]) as client:
        yield client


async def new_module(api, content=GOOD, mid="mod"):
    base = f"{api.base}/knowledgeSpecification"
    created = await api.post(base, json={"id": mid, "name": mid, "iri": "https://example.org/mod", "layer": "Telecom",
                                         "specKind": "ontology", "source": {"kind": "upload"}}, headers=auth(EDITOR))
    assert created.status_code == 201, created.text
    put = await api.put(f"{base}/{mid}/content", content=content, headers={**auth(EDITOR), "Content-Type": "text/turtle"})
    assert put.status_code == 204, put.text
    return f"{base}/{mid}"


async def move(api, url, state, role):
    return await api.patch(url, json={"lifecycleStatus": state}, headers=auth(role))


async def review(api, url, role, **payload):
    payload.setdefault("target", {"kind": "module", "id": url.rsplit("/", 1)[-1]})
    return await api.post(f"{url}/review", json=payload, headers=auth(role))


async def state(api, url):
    return (await api.get(url, headers=auth())).json()["lifecycleStatus"]


async def ask(api, query):
    from app.graphstore import GOVERNANCE_GRAPH
    return bool(api.app.state.graphs.store.query(query))


def test_lifecycles_come_from_the_oas():
    lifecycles = load_lifecycles(Settings().oas_path)
    assert set(lifecycles) == {"KnowledgeSpecification", "Knowledge"}
    assert lifecycles["KnowledgeSpecification"].find("rejected", "draft").role == "ontology-editor"
    assert lifecycles["Knowledge"].find("draft", "proposed").role == "knowledge-editor"


async def test_the_whole_lifecycle_with_the_right_roles(api):
    url = await new_module(api)
    assert (await move(api, url, "proposed", EDITOR)).status_code == 200
    assert (await move(api, url, "inReview", REVIEWER)).status_code == 200
    assert (await move(api, url, "rejected", APPROVER)).status_code == 200
    assert (await move(api, url, "draft", EDITOR)).status_code == 200
    assert (await move(api, url, "proposed", EDITOR)).status_code == 200
    assert (await move(api, url, "inReview", REVIEWER)).status_code == 200
    assert (await move(api, url, "agreed", APPROVER)).status_code == 200
    assert (await move(api, url, "published", APPROVER)).status_code == 200
    assert (await move(api, url, "deprecated", APPROVER)).status_code == 200
    async with api.app.state.sessions() as s:
        audit = (await s.scalars(select(StateTransition).where(StateTransition.target_id == "mod")
                                 .order_by(StateTransition.id))).all()
    assert [(a.from_state, a.to_state) for a in audit] == [
        ("draft", "proposed"), ("proposed", "inReview"), ("inReview", "rejected"), ("rejected", "draft"),
        ("draft", "proposed"), ("proposed", "inReview"), ("inReview", "agreed"), ("agreed", "published"),
        ("published", "deprecated")]
    assert all(a.actor == "alice" for a in audit)
    assert api.app.state.events.types().count("KnowledgeSpecificationStateChangeEvent") == 9
    assert await ask(api, "ASK { GRAPH <urn:tmfc060:governance> { <https://example.org/mod> "
                          "<http://www.w3.org/ns/adms#status> <urn:tmfc060:lifecycle:deprecated> } }")
    assert await ask(api, "ASK { GRAPH <urn:tmfc060:module:mod> { <https://example.org/mod> "
                          "<http://www.w3.org/2002/07/owl#deprecated> true } }")


@pytest.mark.parametrize("start, target, wrong_role", [
    ("draft", "proposed", REVIEWER), ("proposed", "inReview", EDITOR), ("inReview", "agreed", REVIEWER),
    ("inReview", "rejected", EDITOR), ("rejected", "draft", APPROVER), ("agreed", "published", EDITOR),
    ("published", "deprecated", REVIEWER),
])
async def test_each_transition_needs_its_role(api, start, target, wrong_role):
    url = await new_module(api)
    path = {"draft": [], "proposed": [("proposed", EDITOR)],
            "inReview": [("proposed", EDITOR), ("inReview", REVIEWER)],
            "rejected": [("proposed", EDITOR), ("inReview", REVIEWER), ("rejected", APPROVER)],
            "agreed": [("proposed", EDITOR), ("inReview", REVIEWER), ("agreed", APPROVER)],
            "published": [("proposed", EDITOR), ("inReview", REVIEWER), ("agreed", APPROVER), ("published", APPROVER)]}
    for s, role in path[start]:
        assert (await move(api, url, s, role)).status_code == 200
    response = await move(api, url, target, wrong_role)
    assert response.status_code == 403, response.text
    assert await state(api, url) == start


@pytest.mark.parametrize("target", ["inReview", "agreed", "published", "deprecated", "rejected"])
async def test_transitions_not_in_the_lifecycle_get_409(api, target):
    url = await new_module(api)
    response = await move(api, url, target, APPROVER if target != "inReview" else REVIEWER)
    assert response.status_code == 409 and await state(api, url) == "draft"


async def test_mixing_lifecycle_and_fields_is_refused(api):
    url = await new_module(api)
    response = await api.patch(url, json={"lifecycleStatus": "proposed", "description": "x"}, headers=auth(EDITOR))
    assert response.status_code == 400


async def test_review_items_drive_the_workflow(api):
    url = await new_module(api)
    assert (await review(api, url, REVIEWER, kind="comment", text="early")).status_code == 409  # still draft
    proposal = await review(api, url, EDITOR, kind="proposal", text="please review")
    assert proposal.status_code == 201 and await state(api, url) == "proposed"
    assert (await review(api, url, REVIEWER, kind="comment", text="looks fine")).status_code == 201
    assert (await review(api, url, EDITOR, kind="comment", text="me too")).status_code == 403
    await move(api, url, "inReview", REVIEWER)
    assert (await review(api, url, REVIEWER, kind="ruling", ruling="agreed")).status_code == 403
    ruling = await review(api, url, APPROVER, kind="ruling", ruling="agreed", linkStatus="confirmed",
                          text="matches the programme ruling")
    assert ruling.status_code == 201, ruling.text
    assert ruling.json()["approver"] == "alice" and ruling.json()["linkStatus"] == "confirmed"
    body = (await api.get(url, headers=auth())).json()
    assert body["lifecycleStatus"] == "agreed" and body["linkStatus"] == "confirmed"
    assert (await review(api, url, REVIEWER, kind="comment", text="late")).status_code == 409  # closed at ruling
    items = (await api.get(f"{url}/review", headers=auth("knowledge-viewer"))).json()
    assert [i["kind"] for i in items] == ["proposal", "comment", "ruling"]
    rid = ruling.json()["id"]
    assert await ask(api, "ASK { GRAPH <urn:tmfc060:module:mod> { <https://example.org/mod> "
                          "<https://models.tmforum.org/ontology/fto/linkStatus> \"confirmed\" } }")
    assert await ask(api, f"""ASK {{ GRAPH <urn:tmfc060:governance> {{
        <urn:tmfc060:ruling:{rid}> a <http://www.w3.org/ns/prov#Activity> ;
          <http://www.w3.org/ns/prov#used> <https://example.org/mod> ;
          <http://www.w3.org/ns/prov#wasAssociatedWith> <urn:tmfc060:user:alice> ;
          <http://www.w3.org/ns/prov#value> "agreed" ;
          <http://www.w3.org/2000/01/rdf-schema#comment> "matches the programme ruling" }} }}""")


async def test_rejected_returns_to_draft_and_keeps_the_history(api):
    url = await new_module(api)
    await review(api, url, EDITOR, kind="proposal")
    await move(api, url, "inReview", REVIEWER)
    await review(api, url, APPROVER, kind="ruling", ruling="rejected", text="not yet")
    assert (await move(api, url, "draft", EDITOR)).status_code == 200
    items = (await api.get(f"{url}/review", headers=auth())).json()
    assert [(i["kind"], i.get("ruling")) for i in items] == [("proposal", None), ("ruling", "rejected")]


async def test_validation_gate_blocks_agreed(api):
    url = await new_module(api, content=BAD)  # declares another ontology IRI: catalogIntegrity error
    await review(api, url, EDITOR, kind="proposal")
    await move(api, url, "inReview", REVIEWER)
    blocked = await review(api, url, APPROVER, kind="ruling", ruling="agreed")
    assert blocked.status_code == 409 and "catalogIntegrity" in blocked.json()["message"]
    assert await state(api, url) == "inReview"
    items = (await api.get(f"{url}/review", headers=auth())).json()
    assert [i["kind"] for i in items] == ["proposal"]  # no orphan ruling
    assert (await move(api, url, "agreed", APPROVER)).status_code == 409


async def test_validation_gate_can_be_switched_off(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), governance_require_validation=False) as api:
        url = await new_module(api, content=BAD)
        await move(api, url, "proposed", EDITOR)
        await move(api, url, "inReview", REVIEWER)
        assert (await move(api, url, "agreed", APPROVER)).status_code == 200


async def test_federated_modules_are_governed_by_their_source(api, tmp_path):
    import json
    from app.main import _registry_context
    from app.ontology import sync
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "modules.json").write_text(json.dumps({"modules": {"fed": {"label": "Fed", "iri": "https://example.org/fed",
                                                                       "status": "draft"}}}))
    await sync.sync_all(lambda: _registry_context(api.app), [{"name": "s", "mount": {"path": str(repo)}}])
    url = f"{api.base}/knowledgeSpecification/fed"
    assert (await move(api, url, "deprecated", APPROVER)).status_code == 409
    assert (await review(api, url, REVIEWER, kind="comment", text="x")).status_code == 409


async def test_link_status_is_set_by_rulings_only(api):
    url = await new_module(api)
    assert (await api.patch(url, json={"linkStatus": "confirmed"}, headers=auth(EDITOR))).status_code == 400
