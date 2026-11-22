"""Parsers: document bytes -> structural blocks of cleaned text.

A block is ``{"kind": heading|paragraph|list|table|code, "text", "level"?, "page"?}``. ``layout(blocks)`` joins them
into one text (blocks separated by a blank line) and gives each its ``start``/``end`` offsets in it.
"""
import io
import json
import re
from collections import Counter
from html.parser import HTMLParser

from .clean import clean

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
FENCE = re.compile(r"^\s*(```|~~~)")


def block(kind: str, text: str, **extra) -> dict | None:
    text = clean(text)
    return {"kind": kind, "text": text, **extra} if text else None


def layout(blocks: list[dict]) -> tuple[str, list[dict]]:
    parts, offset, out = [], 0, []
    for b in blocks:
        if parts:
            offset += 2
        out.append({**b, "start": offset, "end": offset + len(b["text"])})
        parts.append(b["text"])
        offset += len(b["text"])
    return "\n\n".join(parts), out


# ---- Markdown --------------------------------------------------------------------------------------------------
def markdown(data: bytes) -> list[dict]:
    blocks: list[dict] = []
    para: list[str] = []
    kind = "paragraph"
    in_code = False

    def flush():
        nonlocal para, kind
        if para and (b := block(kind, "\n".join(para))):
            blocks.append(b)
        para, kind = [], "paragraph"

    for line in data.decode("utf-8", errors="replace").splitlines():
        if FENCE.match(line):
            if in_code:
                para.append(line)
                in_code = False
                kind = "code"
                flush()
            else:
                flush()
                in_code, kind = True, "code"
                para.append(line)
            continue
        if in_code:
            para.append(line)
            continue
        if not line.strip():
            flush()
            continue
        if m := HEADING.match(line):
            flush()
            if b := block("heading", m.group(2), level=len(m.group(1))):
                blocks.append(b)
            continue
        if LIST_ITEM.match(line):
            flush()
            if b := block("list", LIST_ITEM.sub("", line, count=1)):
                blocks.append(b)
            continue
        if line.lstrip().startswith("|"):
            if kind != "table":
                flush()
                kind = "table"
            if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line):  # skip the |---| separator row
                para.append(" | ".join(c.strip() for c in line.strip().strip("|").split("|")))
            continue
        if kind != "paragraph":
            flush()
        para.append(line)
    flush()
    return blocks


# ---- HTML ------------------------------------------------------------------------------------------------------
class _Html(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "aside", "noscript", "template", "form", "svg"}
    BLOCKS = {"p": "paragraph", "div": "paragraph", "li": "list", "tr": "table", "pre": "code", "blockquote":
              "paragraph", "dd": "paragraph", "dt": "paragraph", "figcaption": "paragraph", "caption": "paragraph"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.skip = 0
        self.buffer: list[str] = []
        self.kind, self.level = "paragraph", None

    def flush(self):
        text = " | ".join(self.cells) if self.kind == "table" and self.cells else "".join(self.buffer)
        extra = {"level": self.level} if self.kind == "heading" else {}
        if b := block(self.kind, text, **extra):
            self.blocks.append(b)
        self.buffer, self.cells, self.kind, self.level = [], [], "paragraph", None

    cells: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.kind, self.level = "heading", int(tag[1])
        elif tag in self.BLOCKS:
            self.flush()
            self.kind = self.BLOCKS[tag]
        elif tag in ("td", "th"):
            if self.buffer:
                self.cells.append("".join(self.buffer).strip())
            self.buffer = []
        elif tag == "br":
            self.buffer.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("td", "th"):
            self.cells.append("".join(self.buffer).strip())
            self.buffer = []
        elif re.fullmatch(r"h[1-6]", tag) or tag in self.BLOCKS:
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.buffer.append(data if self.kind == "code" else re.sub(r"\s+", " ", data))


def html(data: bytes) -> list[dict]:
    parser = _Html()
    parser.cells = []
    parser.feed(data.decode("utf-8", errors="replace"))
    parser.close()
    parser.flush()
    return parser.blocks


# ---- PDF -------------------------------------------------------------------------------------------------------
def pdf(data: bytes) -> list[dict]:
    from pypdf import PdfReader
    pages = [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]
    lines_per_page = [[clean(line) for line in text.splitlines() if clean(line)] for text in pages]
    # boilerplate: a line on more than half of the pages (running headers and footers, page numbers)
    counts = Counter(re.sub(r"\d+", "#", line) for lines in lines_per_page for line in set(lines))
    repeated = {line for line, n in counts.items() if len(pages) > 1 and n > len(pages) / 2}
    blocks = []
    for number, lines in enumerate(lines_per_page, start=1):
        paragraph: list[str] = []
        for line in lines + [""]:
            if line and re.sub(r"\d+", "#", line) in repeated:
                continue
            if not line:
                if b := block("paragraph", " ".join(paragraph), page=number):
                    blocks.append(b)
                paragraph = []
            elif paragraph and paragraph[-1].endswith((".", ":", "!", "?")) and line[:1].isupper():
                if b := block("paragraph", " ".join(paragraph), page=number):
                    blocks.append(b)
                paragraph = [line]
            else:
                paragraph.append(line)
    return blocks


# ---- JSON ------------------------------------------------------------------------------------------------------
def flatten(value, path: str = "") -> list[str]:
    if isinstance(value, dict):
        return [line for k, v in value.items() for line in flatten(v, f"{path}.{k}" if path else str(k))]
    if isinstance(value, list):
        return [line for i, v in enumerate(value) for line in flatten(v, f"{path}[{i}]")]
    return [f"{path}: {value}"]


def json_document(data: bytes) -> list[dict]:
    """Plain JSON: one block of `path: value` lines per top-level member or item."""
    parsed = json.loads(data)
    items = parsed.items() if isinstance(parsed, dict) else enumerate(parsed if isinstance(parsed, list) else [parsed])
    blocks = []
    for key, value in items:
        if b := block("paragraph", "\n".join(flatten(value, str(key)))):
            blocks.append(b)
    return blocks


PARSERS = {"text/markdown": markdown, "text/html": html, "application/pdf": pdf, "application/json": json_document}
RDF_TYPES = {"text/turtle", "application/ld+json"}
