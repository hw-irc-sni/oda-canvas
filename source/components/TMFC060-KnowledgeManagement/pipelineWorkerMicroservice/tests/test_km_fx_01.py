"""KM-FX-01 Data Processing: a fixture per format, chunk counts and offsets, limits, RDF and TMF inputs."""
import json

import pytest

from worker.steps.chunk import chunk, tokens
from worker.steps.km_fx_01 import handler
from worker.steps.parse import html, json_document, layout, markdown, pdf


def make_pdf(pages: list[list[str]]) -> bytes:
    """A minimal PDF with one Helvetica text line per entry."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        ops = "BT /F1 11 Tf 72 760 Td " + " ".join(f"({line}) Tj 0 -16 Td" for line in lines) + " ET"
        objects.append(f"<< /Length {len(ops)} >>\nstream\n{ops}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objects)} 0 R "
                       "/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for n, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


MARKDOWN = b"""# Site power runbook

Mains power alarms come first.   The battery takes over.

- Check the rectifier
- Check the battery

## Escalation

| Severity | Who |
|---|---|
| critical | NOC |

```
reset --all
```
"""
HTML = b"""<html><head><style>p{}</style><script>var x=1;</script></head><body>
<nav>Home | About</nav><header>Site banner</header>
<h1>Outage report</h1><p>The cell <b>4711</b> went down.</p><ul><li>Power</li><li>Fibre</li></ul>
<table><tr><th>Cell</th><th>State</th></tr><tr><td>4711</td><td>down</td></tr></table>
<footer>Copyright</footer></body></html>"""


def check_offsets(text, chunks):
    for c in chunks:
        assert text[c["start"]:c["end"]] == c["text"]


def test_markdown():
    blocks = markdown(MARKDOWN)
    assert [b["kind"] for b in blocks] == ["heading", "paragraph", "list", "list", "heading", "table", "code"]
    assert blocks[1]["text"] == "Mains power alarms come first. The battery takes over."  # whitespace cleaned
    assert blocks[5]["text"] == "Severity | Who\ncritical | NOC"
    text, laid = layout(blocks)
    chunks = chunk(text, laid)
    assert len(chunks) == 2 and [c["section"] for c in chunks] == ["Site power runbook", "Escalation"]
    check_offsets(text, chunks)


def test_html_drops_boilerplate():
    blocks = html(HTML)
    texts = [b["text"] for b in blocks]
    assert texts == ["Outage report", "The cell 4711 went down.", "Power", "Fibre", "Cell | State", "4711 | down"]
    assert blocks[0] == {"kind": "heading", "text": "Outage report", "level": 1}
    text, laid = layout(blocks)
    chunks = chunk(text, laid)
    assert len(chunks) == 1 and "Copyright" not in text and "Home" not in text and "var x" not in text
    check_offsets(text, chunks)


def test_pdf_with_running_header_and_footer():
    content = [("Check the rectifier first.", "Then the battery bank."),
               ("Escalate to the NOC on call.", "Open an incident."),
               ("Close the incident when power is back.", "Write the report.")]
    pages = [["ACME Network Runbook", f"Page {n}", *lines] for n, lines in enumerate(content, start=1)]
    blocks = pdf(make_pdf(pages))
    text, laid = layout(blocks)
    assert "ACME Network Runbook" not in text and "Page 2" not in text  # on every page: boilerplate
    assert [b["page"] for b in blocks] == [1, 1, 2, 2, 3, 3]
    assert blocks[0]["text"] == "Check the rectifier first."
    chunks = chunk(text, laid)
    assert len(chunks) == 1
    check_offsets(text, chunks)


def test_json_document():
    blocks = json_document(json.dumps({"site": {"name": "S1", "cells": [1, 2]}, "owner": "NOC"}).encode())
    assert [b["text"] for b in blocks] == ["site.name: S1\nsite.cells[0]: 1\nsite.cells[1]: 2", "owner: NOC"]


def test_limits_and_overlap():
    sentences = " ".join(f"Sentence number {i} talks about alarms and cells." for i in range(60))
    blocks = [{"kind": "heading", "text": "Long", "level": 1}, {"kind": "paragraph", "text": sentences}]
    text, laid = layout(blocks)
    chunks = chunk(text, laid, max_tokens=100, overlap_tokens=20)
    assert len(chunks) > 5
    assert all(tokens(len(c["text"])) <= 100 for c in chunks)
    check_offsets(text, chunks)
    for a, b in zip(chunks, chunks[1:]):
        assert b["start"] < a["end"]  # they overlap
        assert a["end"] - b["start"] <= 20 * 4 + 60  # by about the overlap (whole sentences)
        assert b["start"] > a["start"]
    no_overlap = chunk(text, laid, max_tokens=100, overlap_tokens=0)
    assert all(b["start"] >= a["end"] for a, b in zip(no_overlap, no_overlap[1:]))
    huge = "x" * 5000
    cut = chunk(huge, [{"kind": "paragraph", "text": huge, "start": 0, "end": 5000}], max_tokens=100)
    assert all(len(c["text"]) <= 400 for c in cut) and "".join(c["text"] for c in cut) == huge


class FakeClient:
    def __init__(self, docs):
        self.docs = docs

    async def document(self, kb, doc):
        media, data = self.docs[doc]
        return {"mediaType": media}, data


@pytest.mark.parametrize("doc, media, data, expected_chunks", [
    ("d-md", "text/markdown", MARKDOWN, 2),
    ("d-html", "text/html", HTML, 1),
    ("d-pdf", "application/pdf", make_pdf([["Only page.", "Short."]]), 1),
    ("d-json", "application/json", b'{"a": 1, "b": {"c": 2}}', 1),
])
async def test_handler_documents(doc, media, data, expected_chunks):
    run = handler()
    result = await run({"payload": {"kbId": "kb", "documentId": doc}}, FakeClient({doc: (media, data)}))
    assert result["kind"] == "text" and result["mediaType"] == media and len(result["chunks"]) == expected_chunks
    assert result["chunks"][0]["chunkId"] == f"{doc}:0"
    check_offsets(result["text"], result["chunks"])


@pytest.mark.parametrize("media, data", [
    ("text/turtle", b"<urn:a> <urn:b> <urn:c> ."),
    ("application/ld+json", b'{"@id": "urn:a", "urn:b": {"@id": "urn:c"}}'),
])
async def test_rdf_is_passed_through(media, data):
    result = await handler()({"payload": {"kbId": "kb", "documentId": "d"}}, FakeClient({"d": (media, data)}))
    assert result == {"kind": "rdf", "documentId": "d", "mediaType": media, "content": data.decode()}


async def test_tmf_records_and_chunks():
    resource = {"id": "r-1", "href": "/tmf-api/resourceInventoryManagement/v5/resource/r-1", "@type": "LogicalResource",
                "name": "cell 4711", "resourceStatus": "available",
                "resourceCharacteristic": [{"name": "band", "value": "n78"}]}
    alarm = {"id": "a-9", "@type": "Alarm", "alarmType": "equipmentAlarm", "perceivedSeverity": "critical",
             "probableCause": "powerProblem"}
    r1 = await handler()({"payload": {"kbId": "kb", "source": "tmf639", "params": {"resources": [resource]}}}, None)
    assert r1["kind"] == "tmf" and r1["records"][0]["id"] == "r-1" and r1["records"][0]["resource"] == resource
    assert r1["chunks"][0]["text"] == ("TMF639 LogicalResource r-1\nname: cell 4711\n@type: LogicalResource\n"
                                       "resourceStatus: available\nband: n78")
    event = {"eventType": "AlarmCreateEvent", "event": {"alarm": alarm}}
    r2 = await handler()({"payload": {"kbId": "kb", "source": "tmf642", "params": {"event": event}}}, None)
    assert r2["records"][0]["@type"] == "Alarm" and "perceivedSeverity: critical" in r2["chunks"][0]["text"]


async def test_unparseable_documents_fail_for_good():
    from worker.steps import StepError
    with pytest.raises(StepError) as exc:
        await handler()({"payload": {"kbId": "kb", "documentId": "d"}}, FakeClient({"d": ("application/pdf", b"%PDF-x")}))
    assert exc.value.retryable is False
