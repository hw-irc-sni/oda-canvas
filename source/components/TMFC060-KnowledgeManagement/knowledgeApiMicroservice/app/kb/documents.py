"""Documents of a knowledge-base: streamed multipart upload into 1 MiB ``document_blob`` rows, so any worker replica
can read them from Postgres.

- The size cap ``DOCUMENT_MAX_BYTES`` is enforced while streaming (413); a Content-Length over it is refused first.
- The sha256 is computed while streaming; a second upload of the same bytes to the knowledge-base gets 409.
- The media type is one of PDF, Markdown, HTML, Turtle, JSON-LD and JSON (415 otherwise): sniffed where that is
  reliable (``%PDF-``; parseable JSON, JSON-LD when it has ``@context``), else the declared part type, else the file
  extension. A declared type the content contradicts gets 415.
- Delete removes the metadata, the blobs and the document's chunks in Qdrant; triples extracted from it, and their
  provenance, stay in the graph.
"""
import hashlib
import json
import secrets
from pathlib import PurePosixPath

from python_multipart import MultipartParser
from python_multipart.exceptions import ParseError
from python_multipart.multipart import parse_options_header
from qdrant_client import models
from sqlalchemy import select

from ..db.models import BLOB_CHUNK_SIZE, Document, DocumentBlob
from ..errors import ApiError
from .service import KnowledgeBases, bad_request, conflict
from .vectors import collection_name, unavailable

PDF, MARKDOWN, HTML, TURTLE = "application/pdf", "text/markdown", "text/html", "text/turtle"
JSONLD, JSON = "application/ld+json", "application/json"
ALLOWED = {PDF, MARKDOWN, HTML, TURTLE, JSONLD, JSON}
EXTENSIONS = {".pdf": PDF, ".md": MARKDOWN, ".markdown": MARKDOWN, ".html": HTML, ".htm": HTML, ".ttl": TURTLE,
              ".jsonld": JSONLD, ".json": JSON}
GENERIC = {"", "application/octet-stream", "binary/octet-stream"}
MULTIPART_OVERHEAD = 64 * 1024


def too_large(cap: int) -> ApiError:
    return ApiError(413, "413", "Payload Too Large", f"A document may be at most {cap} bytes")


def unsupported(message: str) -> ApiError:
    return ApiError(415, "415", "Unsupported Media Type", message)


def media_type(head: bytes, whole_json: bytes | None, declared: str, filename: str) -> str:
    """Decide the media type of an upload (see the module docstring)."""
    if head.startswith(b"%PDF-"):
        if declared not in GENERIC and declared != PDF:
            raise unsupported(f"The content is a PDF, not {declared}")
        return PDF
    if whole_json is not None:  # the content starts like JSON
        try:
            parsed = json.loads(whole_json)
        except ValueError:
            parsed = None
        if parsed is not None:
            has_context = any("@context" in item for item in (parsed if isinstance(parsed, list) else [parsed])
                              if isinstance(item, dict))
            if declared not in GENERIC and declared not in (JSON, JSONLD):
                raise unsupported(f"The content is JSON, not {declared}")
            return JSONLD if has_context or declared == JSONLD else JSON
    if declared in (PDF, JSON, JSONLD):
        raise unsupported(f"The content is not {declared}")
    if declared not in GENERIC:
        if declared not in ALLOWED:
            raise unsupported(f"Documents are one of {', '.join(sorted(ALLOWED))}, not {declared}")
        return declared
    by_extension = EXTENSIONS.get(PurePosixPath(filename or "").suffix.lower())
    if by_extension in (PDF, JSON, JSONLD):
        raise unsupported(f"The content is not {by_extension}")
    if by_extension is None:
        raise unsupported("Give the part a Content-Type or the file a known extension (.md, .html, .ttl, …)")
    return by_extension


class Upload:
    """State of one streamed multipart upload: the parts' headers, the form fields, and the file's bytes."""

    def __init__(self, cap: int):
        self.cap = cap
        self.part_headers: dict[str, str] = {}
        self.header_field = b""
        self.header_value = b""
        self.current: str | None = None  # the name of the part being read
        self.fields: dict[str, bytearray] = {}
        self.file_seen = False
        self.filename = ""
        self.declared = ""
        self.pending = bytearray()  # file bytes not yet written to a blob row
        self.size = 0
        self.sha = hashlib.sha256()
        self.head = b""
        self.json: bytearray | None = None
        self.json_checked = False

    def callbacks(self) -> dict:
        return {"on_part_begin": self.part_begin, "on_part_data": self.part_data,
                "on_header_field": lambda d, s, e: self._add("header_field", d[s:e]),
                "on_header_value": lambda d, s, e: self._add("header_value", d[s:e]),
                "on_header_end": self.header_end, "on_headers_finished": self.headers_finished}

    def _add(self, attr: str, data: bytes) -> None:
        setattr(self, attr, getattr(self, attr) + data)

    def part_begin(self) -> None:
        self.part_headers, self.current = {}, None

    def header_end(self) -> None:
        # latin-1 keeps the raw bytes: a UTF-8 file name is decoded from them below
        self.part_headers[self.header_field.decode("latin-1").lower()] = self.header_value.decode("latin-1")
        self.header_field = self.header_value = b""

    def headers_finished(self) -> None:
        _, params = parse_options_header(self.part_headers.get("content-disposition", ""))
        name = params.get(b"name", b"").decode("utf-8", "replace")
        self.current = name
        if name == "file":
            if self.file_seen:
                raise bad_request("Send one file per upload")
            self.file_seen = True
            self.filename = params.get(b"filename", b"").decode("utf-8", "replace")
            self.declared = parse_options_header(self.part_headers.get("content-type", ""))[0].decode("latin-1").lower()
        else:
            self.fields[name] = bytearray()

    def part_data(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if self.current != "file":
            field = self.fields.setdefault(self.current or "", bytearray())
            if len(field) + len(chunk) > 64 * 1024:
                raise bad_request("Form fields are limited to 64 KiB")
            field.extend(chunk)
            return
        self.size += len(chunk)
        if self.size > self.cap:
            raise too_large(self.cap)
        self.sha.update(chunk)
        if len(self.head) < 1024:
            self.head = (self.head + chunk)[:1024]
        if not self.json_checked and self.head.lstrip(b"\xef\xbb\xbf \t\r\n"):
            self.json_checked = True
            if self.head.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"{", b"["):
                self.json = bytearray()
        if self.json is not None:
            self.json.extend(chunk)
        self.pending.extend(chunk)

    def field(self, name: str) -> str | None:
        value = self.fields.get(name)
        return value.decode("utf-8", "replace").strip() if value is not None else None


class Documents:
    def __init__(self, kbs: KnowledgeBases):
        self.kbs = kbs
        self.session = kbs.session
        self.settings = kbs.settings

    def href(self, kb_id: str, doc_id: str) -> str:
        return f"{self.settings.base_path}/knowledge/{kb_id}/document/{doc_id}"

    def present(self, d: Document) -> dict:
        out = {"@type": "Document", "id": d.id, "href": self.href(d.knowledge_id, d.id), "name": d.name,
               "description": d.description, "mediaType": d.media_type, "size": d.size, "sha256": d.sha256,
               "uploadDate": d.upload_date.isoformat().replace("+00:00", "Z") if d.upload_date else None,
               "ingestionState": d.ingestion_state}
        if d.last_process_id:
            out["lastIngestion"] = {"@type": "ProcessRef", "id": d.last_process_id}
        return {k: v for k, v in out.items() if v is not None}

    async def get(self, kb_id: str, doc_id: str) -> Document:
        await self.kbs.get_row(kb_id)
        doc = await self.session.get(Document, doc_id)
        if doc is None or doc.knowledge_id != kb_id:
            raise ApiError(404, "404", "Not Found", f"No document '{doc_id}' in '{kb_id}'")
        return doc

    async def list(self, kb_id: str) -> list[dict]:
        await self.kbs.get_row(kb_id)
        docs = (await self.session.scalars(select(Document).where(Document.knowledge_id == kb_id)
                                           .order_by(Document.upload_date, Document.id))).all()
        return [self.present(d) for d in docs]

    async def upload(self, kb_id: str, principal, content_type: str, content_length: str | None, stream) -> dict:
        row = await self.kbs.get_row(kb_id)
        self.kbs.check_writer(row, principal)
        self.kbs.check_writable(row)
        cap = self.settings.document_max_bytes
        if content_length and content_length.isdigit() and int(content_length) > cap + MULTIPART_OVERHEAD:
            raise too_large(cap)
        kind, params = parse_options_header(content_type or "")
        if kind != b"multipart/form-data" or b"boundary" not in params:
            raise unsupported("Upload the document as multipart/form-data with a 'file' part")
        upload = Upload(cap)
        parser = MultipartParser(params[b"boundary"], upload.callbacks())
        doc = Document(id=f"doc-{secrets.token_hex(6)}", knowledge_id=kb_id, name="", media_type="", size=0,
                       sha256="", ingestion_state="pending")
        self.session.add(doc)
        await self.session.flush()  # the blob rows reference it
        seq = 0
        try:
            async for chunk in stream:
                parser.write(chunk)
                while len(upload.pending) >= BLOB_CHUNK_SIZE:
                    self.session.add(DocumentBlob(document_id=doc.id, seq=seq,
                                                  data=bytes(upload.pending[:BLOB_CHUNK_SIZE])))
                    del upload.pending[:BLOB_CHUNK_SIZE]
                    seq += 1
                    await self.session.flush()
            parser.finalize()
            if not upload.file_seen:
                raise bad_request("The upload has no 'file' part")
            if upload.pending:
                self.session.add(DocumentBlob(document_id=doc.id, seq=seq, data=bytes(upload.pending)))
            doc.media_type = media_type(upload.head, bytes(upload.json) if upload.json is not None else None,
                                        upload.declared, upload.filename)
            doc.sha256 = upload.sha.hexdigest()
            existing = await self.session.scalar(select(Document.id).where(
                Document.knowledge_id == kb_id, Document.sha256 == doc.sha256, Document.id != doc.id))
            if existing:
                raise conflict(f"The same content is already the document '{existing}'")
            doc.size = upload.size
            doc.name = upload.field("name") or upload.filename or doc.id
            doc.description = upload.field("description") or None
            await self.session.commit()
        except ParseError as exc:
            await self.session.rollback()
            raise bad_request(f"The multipart body is malformed: {exc}") from exc
        except BaseException:
            await self.session.rollback()
            raise
        await self.session.refresh(doc)
        await self.kbs.publish("AttributeValueChange", row.body)
        return self.present(doc)

    async def content(self, kb_id: str, doc_id: str, sessions):
        """The document and an iterator over its blob rows, read with a session of its own (the response is
        streamed after the request's session has closed)."""
        doc = await self.get(kb_id, doc_id)

        async def chunks():
            async with sessions() as session:
                seq = 0
                while True:
                    data = await session.scalar(select(DocumentBlob.data).where(
                        DocumentBlob.document_id == doc_id, DocumentBlob.seq == seq))
                    if data is None:
                        return
                    yield data
                    seq += 1
        return doc, chunks()

    async def delete(self, kb_id: str, doc_id: str, principal) -> None:
        row = await self.kbs.get_row(kb_id)
        self.kbs.check_writer(row, principal)
        self.kbs.check_writable(row)
        doc = await self.get(kb_id, doc_id)
        if row.store_kind in ("vector", "hybrid"):
            client = self.kbs.vectors.client
            try:
                if await client.collection_exists(collection_name(kb_id)):
                    await client.delete(collection_name(kb_id), points_selector=models.FilterSelector(
                        filter=models.Filter(must=[models.FieldCondition(
                            key="documentId", match=models.MatchValue(value=doc_id))])))
            except Exception as exc:  # noqa: BLE001
                raise unavailable(exc) from exc
        await self.session.delete(doc)  # the blob rows cascade
        await self.session.commit()
        await self.kbs.publish("AttributeValueChange", row.body)
