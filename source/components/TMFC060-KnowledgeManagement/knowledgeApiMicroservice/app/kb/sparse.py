"""The sparse (BM25) side of hybrid search: a small, dependency-free, deterministic text encoder.

Text is split into Unicode word tokens (letters and digits), camelCase and snake_case parts are split too (IRI local
names such as ``PowerAlarm`` give ``power`` and ``alarm``), everything is lowercased and a short English stop-word list
is dropped. Each token is feature-hashed (blake2b) to an index below 2^31. Documents get BM25 term-frequency weights
(k1 = 1.2, b = 0.75 against a fixed average length); queries get weight 1 per distinct token. The IDF part of BM25
comes from Qdrant (the collection's sparse vector uses the ``IDF`` modifier).

The pipeline worker writes chunks with this same encoder, so it must not change without re-indexing.
"""
import hashlib
import re
from collections import Counter

K1, B, AVERAGE_LENGTH = 1.2, 0.75, 120.0
INDEX_SPACE = 2 ** 31
WORD = re.compile(r"[^\W_]+", re.UNICODE)
STOP_WORDS = frozenset("""a an and are as at be but by for from has have if in into is it its of on or such that the
their then there these they this to was were will with""".split())


def split_word(word: str) -> list[str]:
    """camelCase, PascalCase, acronyms and digit runs: 'HTTPServer2Go' -> HTTP, Server, 2, Go (Unicode-aware)."""
    parts, start = [], 0
    for i in range(1, len(word)):
        prev, cur, nxt = word[i - 1], word[i], word[i + 1] if i + 1 < len(word) else ""
        if (prev.islower() and cur.isupper()) or (prev.isdigit() != cur.isdigit()) \
                or (prev.isupper() and cur.isupper() and nxt.islower()):
            parts.append(word[start:i])
            start = i
    parts.append(word[start:])
    return parts


def tokens(text: str) -> list[str]:
    out = []
    for word in WORD.findall(text):
        parts = split_word(word)
        for part in parts:
            token = part.lower()
            if token and token not in STOP_WORDS:
                out.append(token)
        if len(parts) > 1:  # keep the whole compound as well: "poweralarm"
            out.append(word.lower())
    return out


def index(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big") % INDEX_SPACE


def _merge(weights: dict[int, float], token_index: int, weight: float) -> None:
    weights[token_index] = max(weights.get(token_index, 0.0), weight)  # hash collisions keep the larger weight


def encode_document(text: str) -> tuple[list[int], list[float]]:
    counts = Counter(tokens(text))
    length = sum(counts.values()) or 1
    weights: dict[int, float] = {}
    for token, tf in counts.items():
        _merge(weights, index(token), tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / AVERAGE_LENGTH)))
    items = sorted(weights.items())
    return [i for i, _ in items], [w for _, w in items]


def encode_query(text: str) -> tuple[list[int], list[float]]:
    indices = sorted({index(t) for t in tokens(text)})
    return indices, [1.0] * len(indices)
