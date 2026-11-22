"""Term lookups: describe, ranked search, layer chain, visibility (synthetic modules shaped like FTO's)."""
import pytest

from .api import auth, running_api

pytestmark = pytest.mark.integration

EDITOR, REVIEWER, APPROVER = "ontology-editor", "ontology-reviewer", "ontology-approver"
PREFIXES = """@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> . @prefix fto: <https://models.tmforum.org/ontology/fto/> .
@prefix base: <https://example.org/base/> . @prefix tel: <https://example.org/tel/> . @prefix imp: <https://example.org/imp/> .
"""
MODULES = {
    "base": ("Foundational", "base", """<https://example.org/base/> a owl:Ontology ; fto:layer fto:Foundational .
base:Resource a owl:Class ; rdfs:label "Resource" ; skos:definition "Anything managed." ; fto:layer fto:Foundational .
base:Event a owl:Class ; rdfs:label "Event" ; rdfs:comment "Something that happens to a Resource." ."""),
    "tel": ("Telecom", "tel", """<https://example.org/tel/> a owl:Ontology ; fto:layer fto:Telecom .
tel:Alarm a owl:Class ; rdfs:subClassOf base:Event ; rdfs:label "Alarm" ; fto:layer fto:Telecom ;
  skos:definition "A fault notification." ; skos:exactMatch base:Event .
tel:AlarmRecord a owl:Class ; rdfs:subClassOf tel:Alarm ; rdfs:label "Alarm record" .
tel:Loop1 a owl:Class ; rdfs:subClassOf tel:Loop2 . tel:Loop2 a owl:Class ; rdfs:subClassOf tel:Loop1 ."""),
    "imp": ("Implementation", "imp", """<https://example.org/imp/> a owl:Ontology ; fto:layer fto:Implementation .
imp:VendorAlarm a owl:Class ; rdfs:subClassOf tel:Alarm ; rdfs:label "Vendor alarm" ; fto:layer fto:Implementation ;
  fto:linkStatus "provisional" .
tel:Alarm rdfs:comment "Vendors raise it." ."""),
    "multi": ("Foundational", "mx, my", """@prefix mx: <https://example.org/multi/X/> .
@prefix my: <https://example.org/multi/Y/> . @prefix zz: <https://example.org/unlisted/> .
<https://example.org/multi/> a owl:Ontology . mx:Thing a owl:Class ; rdfs:subClassOf my:Root ; rdfs:label "Multi thing" ."""),
    "draft": ("Implementation", "dr", """<https://example.org/draft/> a owl:Ontology .
<https://example.org/draft/Hidden> a owl:Class ; rdfs:label "Hidden alarm" ."""),
}
IRIS = {"base": "https://example.org/base/", "tel": "https://example.org/tel/", "imp": "https://example.org/imp/",
        "draft": "https://example.org/draft/", "multi": "https://example.org/multi/"}


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), governance_require_validation=False) as client:
        base = f"{client.base}/knowledgeSpecification"
        for mid, (layer, prefix, ttl) in MODULES.items():
            r = await client.post(base, json={"id": mid, "name": mid, "iri": IRIS[mid], "prefix": prefix, "layer": layer,
                                              "specKind": "ontology", "source": {"kind": "upload"}}, headers=auth(EDITOR))
            assert r.status_code == 201, r.text
            r = await client.put(f"{base}/{mid}/content", content=(PREFIXES + ttl).encode(),
                                 headers={**auth(EDITOR), "Content-Type": "text/turtle"})
            assert r.status_code == 204, r.text
            if mid == "draft":
                continue
            for state, role in [("proposed", EDITOR), ("inReview", REVIEWER), ("agreed", APPROVER),
                                ("published", APPROVER)]:
                r = await client.patch(f"{base}/{mid}", json={"lifecycleStatus": state}, headers=auth(role))
                assert r.status_code == 200, r.text
        yield client


async def term(api, role="knowledge-viewer", **params):
    return await api.get(f"{api.base}/knowledgeTerm", params=params, headers=auth(role))


async def test_describe(api):
    r = await term(api, iri="tel:Alarm")
    assert r.status_code == 200, r.text
    d = r.json()["description"]
    assert d["iri"] == "https://example.org/tel/Alarm" and d["curie"] == "tel:Alarm"
    assert d["definedIn"] == ["imp", "tel"]
    assert d["labels"] == ["Alarm"] and set(d["definitions"]) == {"A fault notification.", "Vendors raise it."}
    assert d["types"] == ["owl:Class"] and d["parents"] == ["base:Event"]
    assert d["matches"] == [{"predicate": "skos:exactMatch", "object": "base:Event"}]
    assert d["layer"] == "Telecom" and "linkStatus" not in d and "layerChain" not in r.json()
    full = await term(api, iri="<https://example.org/imp/VendorAlarm>")
    assert full.json()["description"]["linkStatus"] == "provisional"
    # like FTO, the layer is the term's own fto:layer
    assert "layer" not in (await term(api, iri="tel:AlarmRecord")).json()["description"]


async def test_describe_errors(api):
    assert (await term(api, iri="Alarm")).status_code == 400  # plain word: use q=
    assert (await term(api, iri="nope:Alarm")).status_code == 400
    assert (await term(api, iri="tel:Nothing")).status_code == 404
    assert (await term(api, iri="tel:Alarm", q="alarm")).status_code == 400
    assert (await term(api)).status_code == 400
    assert (await term(api, q="alarm", limit="x")).status_code == 400
    assert (await term(api, q="alarm", limit="0")).status_code == 400
    assert (await term(api, iri="tel:Alarm", layerChain="maybe")).status_code == 400


async def test_search_is_ranked(api):
    hits = (await term(api, q="alarm")).json()["hits"]
    assert [h["curie"] for h in hits] == ["tel:Alarm", "tel:AlarmRecord", "imp:VendorAlarm"]
    assert hits[0] == {"iri": "https://example.org/tel/Alarm", "curie": "tel:Alarm", "module": "tel",
                       "layer": "Telecom", "via": "label", "text": "Alarm"}
    assert hits[2]["module"] == "imp" and hits[2]["layer"] == "Implementation"
    # IRI local names, then definitions and comments
    hits = (await term(api, q="RESOURCE")).json()["hits"]
    assert [(h["curie"], h["via"]) for h in hits] == [("base:Resource", "label"), ("base:Event", "comment")]
    assert [h["via"] for h in (await term(api, q="loop")).json()["hits"]] == ["iri", "iri"]
    assert len((await term(api, q="alarm", limit="1")).json()["hits"]) == 1
    assert (await term(api, q="zzz")).json()["hits"] == []


async def test_layer_chain(api):
    r = (await term(api, iri="imp:VendorAlarm", layerChain="true")).json()
    assert r["layerChain"] == {"endsAtFoundational": True, "chain": [
        {"term": "imp:VendorAlarm", "module": "imp", "layer": "Implementation", "linkStatus": "provisional"},
        {"term": "tel:Alarm", "module": "tel", "layer": "Telecom"},
        {"term": "base:Event", "module": "base"}]}
    loop = (await term(api, iri="tel:Loop1", layerChain="true")).json()["layerChain"]
    assert [e["term"] for e in loop["chain"]] == ["tel:Loop1", "tel:Loop2"] and not loop["endsAtFoundational"]


async def test_unpublished_modules_are_hidden_by_default(api):
    assert (await term(api, iri="dr:Hidden")).status_code == 404
    assert "dr:Hidden" not in [h["curie"] for h in (await term(api, q="hidden")).json()["hits"]]
    shown = await term(api, iri="dr:Hidden", includeUnpublished="true")
    assert shown.status_code == 200 and shown.json()["description"]["definedIn"] == ["draft"]
    hits = (await term(api, q="hidden", includeUnpublished="true")).json()["hits"]
    assert [h["curie"] for h in hits] == ["dr:Hidden"]


async def test_listed_prefixes_bind_to_the_declared_namespaces(api):
    # a module listing several prefixes (as TIO does) binds each to the namespace its content declares
    d = (await term(api, iri="mx:Thing")).json()["description"]
    assert d["iri"] == "https://example.org/multi/X/Thing" and d["curie"] == "mx:Thing"
    assert d["parents"] == ["my:Root"]
    assert (await term(api, iri="zz:Thing")).status_code == 400  # declared but not listed


async def test_rbac(api):
    assert (await api.get(f"{api.base}/knowledgeTerm", params={"q": "alarm"})).status_code == 401
    assert (await term(api, role="canvasRole", q="alarm")).status_code == 200
