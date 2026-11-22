"""Structure-aware chunking with approximate token limits.

Tokens are estimated as ceil(characters / 4). Blocks are grouped into sections (a heading starts one); within a
section, consecutive blocks are packed into chunks of at most ``max_tokens``. A block over the limit is split on
sentence boundaries, then on words. Each chunk overlaps the previous one in its section by about ``overlap_tokens``
(whole units). Every chunk is a span of the laid-out text: ``text[start:end] == chunk["text"]``.
"""
import math
import re

SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)\s*")
WORD = re.compile(r"\S+\s*")


def tokens(n_chars: int) -> int:
    return math.ceil(n_chars / 4)


def _spans(text: str, start: int, end: int, pattern: re.Pattern) -> list[tuple[int, int]]:
    spans = [(start + m.start(), start + m.end()) for m in pattern.finditer(text[start:end]) if m.group().strip()]
    return [(s, s + len(text[s:e].rstrip())) for s, e in spans]


def _units(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    if end - start <= max_chars:
        return [(start, end)]
    out = []
    for s, e in _spans(text, start, end, SENTENCE):
        out.extend([(s, e)] if e - s <= max_chars else _words(text, s, e, max_chars))
    return out


def _words(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    out, cur = [], None
    for s, e in _spans(text, start, end, WORD):
        if cur and e - cur[0] > max_chars:
            out.append(cur)
            cur = None
        cur = (cur[0], e) if cur else (s, e)
        if e - s > max_chars:  # a single huge "word": cut it
            out.extend((i, min(i + max_chars, e)) for i in range(s, e, max_chars))
            cur = None
    if cur:
        out.append(cur)
    return out


def chunk(text: str, blocks: list[dict], max_tokens: int = 400, overlap_tokens: int = 60) -> list[dict]:
    max_chars, overlap_chars = max_tokens * 4, overlap_tokens * 4
    sections: list[tuple[str | None, list[dict]]] = []
    for b in blocks:
        if b["kind"] == "heading" or not sections:
            sections.append((b["text"] if b["kind"] == "heading" else None, []))
        sections[-1][1].append(b)
    chunks = []
    for title, members in sections:
        units = [u for b in members for u in _units(text, b["start"], b["end"], max_chars)]
        i = 0
        while i < len(units):
            j = i
            while j + 1 < len(units) and units[j + 1][1] - units[i][0] <= max_chars:
                j += 1
            start, end = units[i][0], units[j][1]
            chunks.append({"text": text[start:end], "start": start, "end": end, "section": title,
                           "tokens": tokens(end - start)})
            if j + 1 >= len(units):
                break
            k = j  # the next chunk starts with the last units of this one, up to the overlap
            while k > i and end - units[k][0] <= overlap_chars:
                k -= 1
            nxt = k + 1 if end - units[k][0] > overlap_chars else k
            i = max(nxt, i + 1) if nxt <= j else j + 1
    for n, c in enumerate(chunks):
        c["seq"] = n
    return chunks
