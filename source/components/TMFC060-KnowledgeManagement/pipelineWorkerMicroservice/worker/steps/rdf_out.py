"""Triples of the step results ([s, p, o, "iri" | "literal", datatype | "@lang" | None]) as N-Triples (send them as
text/turtle: N-Triples is a subset of Turtle)."""


def _literal(value: str) -> str:
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
               .replace("\t", "\\t"))
    return f'"{escaped}"'


def term(value: str, kind: str = "iri", extra: str | None = None) -> str:
    if kind == "iri":
        return f"<{value}>"
    if extra and extra.startswith("@"):
        return _literal(value) + extra
    if extra:
        return f"{_literal(value)}^^<{extra}>"
    return _literal(value)


def ntriples(triples: list) -> bytes:
    lines = []
    for t in triples:
        s, p, o, kind = t[:4]
        extra = t[4] if len(t) > 4 else None
        lines.append(f"{term(s)} {term(p)} {term(o, kind, extra)} .")
    return ("\n".join(lines) + "\n").encode() if lines else b""
