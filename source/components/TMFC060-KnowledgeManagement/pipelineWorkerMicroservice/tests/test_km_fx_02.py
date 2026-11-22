"""KM-FX-02 Information Extraction with a mocked model: ontology-only triples, proposals, lifting of RDF and TMF."""
import json

import pytest

from worker.client import ModelUnavailable
from worker.steps import StepError
from worker.steps.km_fx_02 import entity_iri, handler
from worker.steps.vocabulary import OWL, RDF, RDFS, Vocabulary

AS = "https://example.org/as#"
ICM = "http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/"
T639 = "https://models.tmforum.org/ontology/open-api/tmf639/"
VOCAB = [  # (term, type, label, domain, range, super)
    (AS + "Alarm", OWL + "Class", "Alarm", None, None, None),
    (AS + "PowerAlarm", OWL + "Class", "Power alarm", None, None, AS + "Alarm"),
    (AS + "Cell", OWL + "Class", "Cell", None, None, None),
    (AS + "affects", OWL + "ObjectProperty", "affects", AS + "Alarm", AS + "Cell", None),
    (AS + "severity", OWL + "DatatypeProperty", "severity", AS + "Alarm", None, None),
    (ICM + "Intent", OWL + "Class", "Intent", None, None, None),
    (ICM + "DeliveryExpectation", OWL + "Class", "Delivery expectation", None, None, None),
    (ICM + "hasExpectation", OWL + "ObjectProperty", "has expectation", None, None, None),
    (ICM + "target", OWL + "ObjectProperty", "target", None, None, None),
    (T639 + "LogicalResource", OWL + "Class", "Logical resource", None, None, None),
    (T639 + "name", OWL + "DatatypeProperty", "name", None, None, None),
]


def bindings(vocab):
    rows = []
    for term, kind, label, domain, range_, sup in vocab:
        row = {"t": {"type": "uri", "value": term}, "type": {"type": "uri", "value": kind},
               "label": {"type": "literal", "value": label}}
        if domain:
            row["domain"] = {"type": "uri", "value": domain}
        if range_:
            row["range"] = {"type": "uri", "value": range_}
        if sup:
            row["super"] = {"type": "uri", "value": sup}
        rows.append(row)
    return {"head": {"vars": []}, "results": {"bindings": rows}}


class FakeClient:
    def __init__(self, answers=(), vocab=VOCAB):
        self.answers = list(answers)
        self.vocab = vocab
        self.prompts = []
        self.queries = 0

    async def query(self, kb, sparql):
        self.queries += 1
        if "DISTINCT ?t ?role" in sparql and self.vocab:  # terms declared with other types, and superclasses
            return {"head": {}, "results": {"bindings": [
                {"t": {"type": "uri", "value": ICM + "metaTyped"}, "role": {"type": "literal", "value": "term"}},
                {"t": {"type": "uri", "value": AS + "Event"}, "role": {"type": "literal", "value": "class"}}]}}
        return bindings(self.vocab)

    async def chat(self, messages, **options):
        assert options["response_format"] == {"type": "json_object"}
        self.prompts.append(messages)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


def text_task(*chunks):
    return {"payload": {"kbId": "kb", "documentId": "doc-1"}, "input": {
        "kind": "text", "documentId": "doc-1", "mediaType": "text/markdown", "text": " ".join(chunks),
        "chunks": [{"chunkId": f"doc-1:{i}", "text": t, "start": 0, "end": len(t), "section": None}
                   for i, t in enumerate(chunks)]}}


ANSWER = {"entities": [{"id": "e1", "type": AS + "PowerAlarm", "label": "Mains failure"},
                       {"id": "e2", "type": "Cell", "label": "Cell 4711"},  # a local name is resolved
                       {"id": "e3", "type": AS + "Battery", "label": "Battery B"}],  # not in the ontology
          "relations": [{"subject": "e1", "predicate": AS + "affects", "object": "e2"},
                        {"subject": "e1", "predicate": AS + "drains", "object": "e2"}],  # unknown predicate
          "attributes": [{"subject": "e1", "predicate": "severity", "value": "critical"}]}


async def test_output_triples_use_only_ontology_terms():
    client = FakeClient([ANSWER, {"entities": [{"id": "x", "type": "Cell", "label": "Cell 4711"}]}])
    result = await handler()(text_task("Mains failure hits cell 4711.", "Cell 4711 is down."), client)
    alarm, cell = entity_iri("kb", AS + "PowerAlarm", "Mains failure"), entity_iri("kb", AS + "Cell", "Cell 4711")
    assert alarm == "urn:tmfc060:kb:kb:entity:PowerAlarm:mains-failure"
    triples = {tuple(t[:3]) for t in result["triples"]}
    assert triples == {(alarm, RDF + "type", AS + "PowerAlarm"), (alarm, RDFS + "label", "Mains failure"),
                       (cell, RDF + "type", AS + "Cell"), (cell, RDFS + "label", "Cell 4711"),
                       (alarm, AS + "affects", cell), (alarm, AS + "severity", "critical")}
    allowed = {t for t, *_ in VOCAB} | {RDF + "type", RDFS + "label"}
    assert all(t[1] in allowed for t in result["triples"])
    assert all(t[2] in allowed for t in result["triples"] if t[1] == RDF + "type")
    assert {(p["term"], p["role"]) for p in result["proposedTerms"]} == {(AS + "Battery", "class"),
                                                                          (AS + "drains", "predicate")}
    assert result["dropped"] == 2
    # the same entity in two chunks gets one IRI, linked to both chunks
    cell_entity = next(e for e in result["entities"] if e["iri"] == cell)
    assert cell_entity["chunkIds"] == ["doc-1:0", "doc-1:1"]
    assert result["chunks"][1]["entityIri"] == [cell] and result["chunks"][0]["classIri"] == [AS + "Cell",
                                                                                              AS + "PowerAlarm"]
    assert "CLASS <https://example.org/as#PowerAlarm>" in client.prompts[0][0]["content"]


async def test_invalid_json_is_retried_once():
    client = FakeClient(["not json", ANSWER])
    result = await handler()(text_task("Mains failure."), client)
    assert len(result["entities"]) == 2 and len(client.prompts) == 2
    with pytest.raises(StepError) as exc:
        await handler()(text_task("Again."), FakeClient(["nope", "still not"]))
    assert exc.value.retryable
    with pytest.raises(StepError) as exc:
        await handler()(text_task("x"), FakeClient([ModelUnavailable("no route")]))
    assert exc.value.retryable


async def test_vocabulary_selection():
    many = VOCAB + [(f"{AS}Thing{i}", OWL + "Class", f"Thing {i}", None, None, None) for i in range(200)]
    vocab = Vocabulary.from_results(bindings(many))
    chosen = {t.iri for t in vocab.select("A power alarm affects the cell", 5)}
    assert {AS + "PowerAlarm", AS + "affects", AS + "Cell", AS + "Alarm"} <= chosen  # Alarm as a superclass
    assert len(chosen) <= 5 + 2
    assert len(Vocabulary.from_results(bindings(VOCAB)).select("x", 150)) == len(VOCAB)


INTENT_TTL = f"""@prefix icm: <{ICM}> . @prefix ex: <https://example.org/intent/> .
ex:I1 a icm:Intent ; icm:hasExpectation ex:E1 .
ex:E1 a icm:DeliveryExpectation ; icm:target ex:Region1 ; ex:priority "high" ."""


async def test_tmf921_intent_is_lifted_to_the_expected_triples():
    intent = {"id": "I1", "@type": "Intent", "href": "https://km.example/tmf-api/intent/v5/intent/I1",
              "name": "Energy saving in Region1",
              "expression": {"@type": "TurtleExpression", "expressionValue": INTENT_TTL}}
    task = {"payload": {"kbId": "kb", "source": "tmf921"}, "input": {
        "kind": "tmf", "source": "tmf921", "records": [{"source": "tmf921", "@type": "Intent", "id": "I1",
                                                        "resource": intent}],
        "chunks": [{"chunkId": "tmf921:I1", "text": "TMF921 Intent I1", "start": None, "end": None}]}}
    result = await handler()(task, FakeClient())
    ex = "https://example.org/intent/"
    assert {tuple(t[:3]) for t in result["triples"]} == {
        (ex + "I1", RDF + "type", ICM + "Intent"), (ex + "I1", ICM + "hasExpectation", ex + "E1"),
        (ex + "E1", RDF + "type", ICM + "DeliveryExpectation"), (ex + "E1", ICM + "target", ex + "Region1")}
    proposed = {p["term"] for p in result["proposedTerms"]}
    assert ex + "priority" in proposed  # not in the ontology
    assert "https://models.tmforum.org/ontology/open-api/tmf921/Intent" in proposed  # the generic mapping's class


async def test_generic_tmf639_mapping():
    resource = {"id": "r-1", "@type": "LogicalResource", "name": "cell 4711", "resourceStatus": "available"}
    task = {"payload": {"kbId": "kb", "source": "tmf639"}, "input": {
        "kind": "tmf", "source": "tmf639", "records": [{"source": "tmf639", "resource": resource}],
        "chunks": [{"chunkId": "tmf639:r-1", "text": "x"}]}}
    result = await handler()(task, FakeClient())
    s = "urn:tmf:tmf639:LogicalResource:r-1"
    assert {tuple(t[:3]) for t in result["triples"]} == {(s, RDF + "type", T639 + "LogicalResource"),
                                                         (s, T639 + "name", "cell 4711")}
    assert result["chunks"][0]["entityIri"] == [s] and result["chunks"][0]["classIri"] == [T639 + "LogicalResource"]
    assert T639 + "resourceStatus" in {p["term"] for p in result["proposedTerms"]}


async def test_rdf_documents_are_lifted_directly():
    ttl = f"""@prefix as: <{AS}> . <urn:a1> a as:PowerAlarm ; as:affects [ a as:Cell ] ; as:other "x" ."""
    task = {"payload": {"kbId": "kb", "documentId": "d"}, "input": {"kind": "rdf", "documentId": "d",
                                                                    "mediaType": "text/turtle", "content": ttl}}
    result = await handler()(task, FakeClient())
    assert len(result["triples"]) == 3 and all(t[1] != AS + "other" for t in result["triples"])
    assert any(t[0].startswith("urn:tmfc060:kb:kb:bnode:") for t in result["triples"])
    remote = {"payload": {"kbId": "kb", "documentId": "d"}, "input": {
        "kind": "rdf", "documentId": "d", "mediaType": "application/ld+json",
        "content": json.dumps({"@context": "https://schema.org/", "@id": "urn:a"})}}
    with pytest.raises(StepError) as exc:
        await handler()(remote, FakeClient())
    assert not exc.value.retryable


async def test_no_classes_no_model_calls():
    client = FakeClient(vocab=[])
    result = await handler()(text_task("Anything."), client)
    assert result["triples"] == [] and client.prompts == []


async def test_lists_meta_typed_terms_and_local_schema():
    ttl = f"""@prefix as: <{AS}> . @prefix icm: <{ICM}> . @prefix owl: <{OWL}> .
@prefix rdfs: <{RDFS}> . @prefix ex: <https://example.org/x/> .
ex:I a icm:Intent ; icm:hasExpectation ( ex:E1 ex:E2 ) ; icm:metaTyped "v" .
ex:Local a owl:Class ; rdfs:subClassOf as:Alarm .
ex:onSite a owl:ObjectProperty ; rdfs:range as:Cell .
ex:A1 a ex:Local ; ex:onSite ex:C1 ; ex:undeclared "x" .
ex:Ev a as:Event ."""
    task = {"payload": {"kbId": "kb", "documentId": "d"}, "input": {"kind": "rdf", "documentId": "d",
                                                                    "mediaType": "text/turtle", "content": ttl}}
    result = await handler()(task, FakeClient())
    predicates = {t[1] for t in result["triples"]}
    assert RDF + "first" in predicates and RDF + "rest" in predicates  # the list survives
    assert ICM + "metaTyped" in predicates  # declared by the ontology with another type
    kept = {tuple(t[:3]) for t in result["triples"]}
    x = "https://example.org/x/"
    assert (x + "Local", RDFS + "subClassOf", AS + "Alarm") in kept  # local schema kept
    assert (x + "A1", RDF + "type", x + "Local") in kept and (x + "A1", x + "onSite", x + "C1") in kept
    assert (x + "Ev", RDF + "type", AS + "Event") in kept  # a class used only as a superclass in the ontology
    assert {p["term"] for p in result["proposedTerms"]} == {x + "undeclared"}
