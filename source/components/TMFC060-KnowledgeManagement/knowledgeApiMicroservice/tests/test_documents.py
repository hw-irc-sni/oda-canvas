"""KB documents: streamed upload into Postgres blobs, download, size cap, dedupe, media types, delete cascade."""
import json
import os

import pytest
from qdrant_client import QdrantClient, models
from sqlalchemy import func, select

from app.db.models import Document, DocumentBlob, Knowledge

from .api import auth, running_api

pytestmark = pytest.mark.integration

EDITOR = "knowledge-editor"


@pytest.fixture
async def api(postgres_url, qdrant_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), qdrant_url=qdrant_url,
                           embedding_dimension=4) as client:
        client.qdrant = QdrantClient(url=qdrant_url)
        if client.qdrant.collection_exists("tmfc060-kb-kb"):  # Qdrant outlives the per-test database
            client.qdrant.delete_collection("tmfc060-kb-kb")
        r = await client.post(f"{client.base}/knowledge", json={"id": "kb", "name": "KB"}, headers=auth(EDITOR))
        assert r.status_code == 201, r.text
        yield client


def docs(api, kb="kb"):
    return f"{api.base}/knowledge/{kb}/document"


async def upload(api, content: bytes, filename="runbook.md", media="text/markdown", role=EDITOR, **fields):
    return await api.post(docs(api), files={"file": (filename, content, media)}, data=fields, headers=auth(role))


async def counts(api):
    async with api.app.state.sessions() as s:
        return (await s.scalar(select(func.count()).select_from(Document)),
                await s.scalar(select(func.count()).select_from(DocumentBlob)))


async def test_upload_list_download_round_trip(api):
    content = os.urandom(2 * 1024 * 1024 + 12345)  # three blob rows
    r = await upload(api, content, "big notes.md", "text/markdown", name="Site power runbook", description="Runbook")
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["name"] == "Site power runbook" and doc["description"] == "Runbook"
    assert doc["mediaType"] == "text/markdown" and doc["size"] == len(content) and doc["ingestionState"] == "pending"
    import hashlib
    assert doc["sha256"] == hashlib.sha256(content).hexdigest()
    assert doc["href"].endswith(f"/knowledge/kb/document/{doc['id']}")
    assert await counts(api) == (1, 3)
    listed = await api.get(docs(api), headers=auth("knowledge-viewer"))
    assert [d["id"] for d in listed.json()] == [doc["id"]] and listed.headers["x-total-count"] == "1"
    assert (await api.get(f"{docs(api)}/{doc['id']}", headers=auth())).json() == doc
    got = await api.get(f"{docs(api)}/{doc['id']}/content", headers=auth())
    assert got.status_code == 200 and got.content == content
    assert got.headers["content-type"].startswith("text/markdown")
    assert "big%20notes.md" not in got.headers["content-disposition"]  # the name given, not the file name
    assert "Site%20power%20runbook" in got.headers["content-disposition"]
    assert (await api.get(f"{api.base}/knowledge/kb", headers=auth())).json()["statistics"]["documents"] == 1
    plain = await upload(api, b"# title\n", "x.md")
    assert plain.json()["name"] == "x.md"  # defaults to the file name


async def test_oversize_is_refused_with_nothing_stored(postgres_url, qdrant_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), qdrant_url=qdrant_url,
                           document_max_bytes=100_000) as api:
        await api.post(f"{api.base}/knowledge", json={"id": "kb", "name": "KB", "storeKind": "graph"},
                       headers=auth(EDITOR))
        assert (await upload(api, b"a" * 99_000)).status_code == 201
        # by Content-Length
        r = await upload(api, b"b" * 300_000)
        assert r.status_code == 413
        # while streaming: a chunked body without Content-Length
        boundary = "XyZ"

        async def body():
            yield (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"s.md\"\r\n"
                   "Content-Type: text/markdown\r\n\r\n").encode()
            for _ in range(20):
                yield b"c" * 10_000
            yield f"\r\n--{boundary}--\r\n".encode()
        r = await api.post(docs(api), content=body(),
                           headers={**auth(EDITOR), "Content-Type": f"multipart/form-data; boundary={boundary}"})
        assert r.status_code == 413, r.text
        assert await counts(api) == (1, 1)


async def test_duplicates_are_refused(api):
    first = await upload(api, b"# same\n", "a.md")
    second = await upload(api, b"# same\n", "b.md")
    assert second.status_code == 409 and first.json()["id"] in second.json()["message"]
    assert await counts(api) == (1, 1)


@pytest.mark.parametrize("content, filename, declared, expected", [
    (b"%PDF-1.7\n...", "x.bin", "application/octet-stream", "application/pdf"),
    (b"%PDF-1.7\n...", "x.pdf", "application/pdf", "application/pdf"),
    (json.dumps({"@context": {"a": "urn:a#"}, "a": 1}).encode(), "x.json", "application/json", "application/ld+json"),
    (json.dumps([{"k": 1}]).encode(), "x.json", "application/octet-stream", "application/json"),
    (b"@prefix a: <urn:a#> . a:x a a:Y .", "x.ttl", "application/octet-stream", "text/turtle"),
    (b"<html><body>hi</body></html>", "page.htm", "application/octet-stream", "text/html"),
    (b"<p>hi</p>", "x", "text/html", "text/html"),
    (b"%PDF-1.7\n...", "x.md", "text/markdown", 415),  # declared type contradicted by the content
    (b"not json", "x.json", "application/json", 415),
    (b"not a pdf", "x.pdf", "application/octet-stream", 415),
    (b"\x89PNG....", "x.png", "image/png", 415),
    (b"data", "x.bin", "application/octet-stream", 415),
])
async def test_media_types(api, content, filename, declared, expected):
    r = await upload(api, content, filename, declared)
    if isinstance(expected, int):
        assert r.status_code == expected, r.text
    else:
        assert r.status_code == 201 and r.json()["mediaType"] == expected, r.text


async def test_bad_uploads(api):
    r = await api.post(docs(api), data={"name": "no file"}, files={"other": ("x", b"x", "text/plain")},
                       headers=auth(EDITOR))
    assert r.status_code == 400
    r = await api.post(docs(api), content=b"{}", headers={**auth(EDITOR), "Content-Type": "application/json"})
    assert r.status_code == 415
    assert (await upload(api, b"# x", role="knowledge-viewer")).status_code == 403
    assert (await api.get(f"{docs(api)}/doc-nope", headers=auth())).status_code == 404
    assert (await api.get(f"{api.base}/knowledge/nope/document", headers=auth())).status_code == 404


async def test_delete_cascades_to_blobs_and_chunks(api):
    a = (await upload(api, os.urandom(1_500_000), "a.md")).json()
    b = (await upload(api, b"# other", "b.md")).json()
    collection = "tmfc060-kb-kb"
    api.qdrant.upsert(collection, points=[
        models.PointStruct(id=i, vector={"dense": [0.1, 0.2, 0.3, 0.4]}, payload={"documentId": doc})
        for i, doc in enumerate([a["id"], a["id"], b["id"]], start=1)])
    assert await counts(api) == (2, 3)
    assert (await api.delete(f"{docs(api)}/{a['id']}", headers=auth("knowledge-viewer"))).status_code == 403
    assert (await api.delete(f"{docs(api)}/{a['id']}", headers=auth(EDITOR))).status_code == 204
    assert await counts(api) == (1, 1)
    remaining = api.qdrant.scroll(collection, with_payload=True)[0]
    assert [p.payload["documentId"] for p in remaining] == [b["id"]]
    assert (await api.get(f"{docs(api)}/{a['id']}", headers=auth())).status_code == 404
    # deleting the knowledge-base removes its documents
    assert (await api.delete(f"{api.base}/knowledge/kb", headers=auth(EDITOR))).status_code == 204
    assert await counts(api) == (0, 0)


async def test_deprecated_knowledge_base_is_read_only(api):
    doc = (await upload(api, b"# kept", "k.md")).json()
    async with api.app.state.sessions() as s:
        row = await s.get(Knowledge, "kb")
        row.lifecycle_status = "deprecated"
        row.body = {**row.body, "lifecycleStatus": "deprecated"}
        await s.commit()
    assert (await upload(api, b"# new", "n.md")).status_code == 409
    assert (await api.delete(f"{docs(api)}/{doc['id']}", headers=auth(EDITOR))).status_code == 409
    assert (await api.get(f"{docs(api)}/{doc['id']}/content", headers=auth())).content == b"# kept"
