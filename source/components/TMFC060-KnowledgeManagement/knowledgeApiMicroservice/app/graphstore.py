"""The pyoxigraph RDF store. The API process is its only writer (single-writer RocksDB).

Named graphs: one per ontology module (``urn:tmfc060:module:{id}``), one per knowledge-base
(``urn:tmfc060:kb:{id}``), and the governance graph (``urn:tmfc060:governance``).
"""
from pyoxigraph import NamedNode, RdfFormat, Store

from .errors import ApiError

GOVERNANCE_GRAPH = "urn:tmfc060:governance"
MEDIA_TYPES = {"text/turtle": RdfFormat.TURTLE, "application/ld+json": RdfFormat.JSON_LD}


def module_graph(module_id: str) -> str:
    return f"urn:tmfc060:module:{module_id}"


def kb_graph(kb_id: str) -> str:
    return f"urn:tmfc060:kb:{kb_id}"


def rdf_format(media_type: str) -> RdfFormat:
    base = media_type.split(";")[0].strip().lower()
    if base not in MEDIA_TYPES:
        raise ApiError(415, "415", "Unsupported Media Type", f"Use one of {', '.join(MEDIA_TYPES)}")
    return MEDIA_TYPES[base]


class GraphStore:
    def __init__(self, store: Store):
        self.store = store

    def _parse_into(self, graph: str, data: bytes, media_type: str) -> None:
        try:
            self.store.load(data, rdf_format(media_type), to_graph=NamedNode(graph))
        except (SyntaxError, ValueError) as exc:
            raise ApiError(400, "400", "Bad Request", f"Invalid RDF: {exc}") from exc

    def merge(self, graph: str, data: bytes, media_type: str) -> None:
        """Add the triples to the graph (Graph Store Protocol POST)."""
        self._parse_into(graph, data, media_type)

    def replace(self, graph: str, data: bytes, media_type: str) -> None:
        """Replace the graph with the triples (Graph Store Protocol PUT). The parse happens in a scratch graph
        first, so invalid content leaves the graph unchanged."""
        scratch = graph + "#replace"
        self.store.clear_graph(NamedNode(scratch))
        try:
            self._parse_into(scratch, data, media_type)
            self.store.update(f"DROP SILENT GRAPH <{graph}>; MOVE SILENT GRAPH <{scratch}> TO <{graph}>")
        finally:
            self.remove(scratch)

    def clear(self, graph: str) -> None:
        """Remove every triple of the graph (Graph Store Protocol DELETE)."""
        self.store.clear_graph(NamedNode(graph))

    def remove(self, graph: str) -> None:
        """Remove the graph itself, for example when its knowledge-base or module is deleted."""
        if self.store.contains_named_graph(NamedNode(graph)):
            self.store.remove_graph(NamedNode(graph))

    def export(self, graph: str, media_type: str = "text/turtle") -> bytes:
        return self.store.dump(format=rdf_format(media_type), from_graph=NamedNode(graph))

    def count(self, graph: str) -> int:
        result = self.store.query("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }", default_graph=NamedNode(graph))
        return int(next(iter(result))["n"].value)
