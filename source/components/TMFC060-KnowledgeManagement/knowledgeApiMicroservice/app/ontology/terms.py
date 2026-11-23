"""Term lookups across the ontology modules: describe, search and layer chain.

Ported from the FTO MCP server (tfo ``mcp/server.py``: ``fto_describe``, ``fto_search``, ``fto_layer_of``), over the
module graphs of the registry. By default only published and deprecated modules are looked in.

Differences from FTO, by design:
- ``iri`` must be an IRI or a CURIE (FTO falls back to the top search hit for a plain word);
- search is ranked (exact label, label prefix, label substring, IRI local name, definition or comment) and also
  matches IRI local names; FTO returns the first substring matches in store order;
- the layer chain follows the first parent by CURIE order (FTO: store order), and ``endsAtFoundational`` is true when
  the last term is declared in a Foundational module (FTO: in the ``ofo:`` namespace).
"""
import re
from dataclasses import dataclass

from pyoxigraph import Literal, NamedNode
from sqlalchemy import select

from ..dataset import VISIBLE_STATES
from ..db.models import KnowledgeSpecification as SpecRow
from ..errors import ApiError
from ..graphstore import module_graph
from .mappings import prefix_map

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
FTO = "https://models.tmforum.org/ontology/fto/"
LABELS = (RDFS + "label", SKOS + "prefLabel", SKOS + "altLabel")
DEFINITIONS = (SKOS + "definition", RDFS + "comment")
MATCHES = (SKOS + "exactMatch", SKOS + "closeMatch", SKOS + "broader")
CLASS_TYPES = (OWL + "Class", RDFS + "Class")
MAX_CHAIN = 12


@dataclass(frozen=True)
class Module:
    id: str
    layer: str | None
    graph: NamedNode


def local_name(iri: str) -> str:
    return re.split(r"[/#]", iri.rstrip("/#"))[-1]


def value(term) -> str:
    return term.value


class Terms:
    def __init__(self, session, store):
        self.session = session
        self.store = store

    async def load(self, include_unpublished: bool) -> None:
        rows = (await self.session.scalars(select(SpecRow).order_by(SpecRow.id))).all()
        self.prefixes = prefix_map(rows)
        self.modules = [Module(r.id, r.layer, NamedNode(module_graph(r.id))) for r in rows
                        if include_unpublished or r.lifecycle_status in VISIBLE_STATES]
        self.by_graph = {m.graph.value: m for m in self.modules}

    # ---- IRIs ----------------------------------------------------------------------------------------------
    def curie(self, iri: str) -> str:
        for prefix, ns in sorted(self.prefixes.items(), key=lambda kv: -len(kv[1])):
            if iri.startswith(ns) and len(iri) > len(ns):
                return f"{prefix}:{iri[len(ns):]}"
        return iri

    def expand(self, term: str) -> str:
        term = term.strip().strip("<>")
        if re.match(r"^[a-z][a-z0-9+.-]*://", term) or term.startswith("urn:"):
            return term
        prefix, sep, local = term.partition(":")
        if sep and prefix in self.prefixes:
            return self.prefixes[prefix] + local
        if sep:  # a CURIE whose prefix no module declares: no such term
            raise ApiError(404, "404", "Not Found", f"No module declares the prefix of '{term}'")
        raise ApiError(400, "400", "Bad Request",
                       f"'{term}' is neither an IRI nor a CURIE; search text with /knowledgeTerm?q=")

    def quads(self, subject=None, predicate=None):
        for module in self.modules:
            for quad in self.store.quads_for_pattern(subject, predicate, None, module.graph):
                yield module, quad

    # ---- describe ------------------------------------------------------------------------------------------
    def describe(self, iri: str) -> dict | None:
        out = {"iri": iri, "curie": self.curie(iri), "definedIn": [], "labels": [], "definitions": [], "types": [],
               "parents": [], "matches": []}
        layer = link_status = None
        declared_in = []
        found = False
        for module, quad in self.quads(NamedNode(iri)):
            found = True
            p, o = quad.predicate.value, quad.object
            if module.id not in out["definedIn"]:
                out["definedIn"].append(module.id)
            if p in LABELS:
                if o.value not in out["labels"]:
                    out["labels"].append(o.value)
            elif p in DEFINITIONS:
                if o.value not in out["definitions"]:
                    out["definitions"].append(o.value)
            elif p == RDF + "type":
                curie = self.curie(o.value)
                if curie not in out["types"]:
                    out["types"].append(curie)
                if o.value in CLASS_TYPES and module.id not in declared_in:
                    declared_in.append(module.id)
            elif p == RDFS + "subClassOf" and isinstance(o, NamedNode):
                curie = self.curie(o.value)
                if curie not in out["parents"]:
                    out["parents"].append(curie)
            elif p in MATCHES:
                out["matches"].append({"predicate": self.curie(p), "object": self.curie(o.value)})
            elif p == FTO + "layer":
                layer = local_name(o.value)
            elif p == FTO + "linkStatus":
                link_status = o.value
        if not found:
            return None
        if layer:
            out["layer"] = layer
        if link_status:
            out["linkStatus"] = link_status
        out["_declaredIn"] = declared_in
        return out

    # ---- search --------------------------------------------------------------------------------------------
    def search(self, text: str, limit: int) -> list[dict]:
        needle = text.strip().lower()
        best: dict[str, tuple] = {}

        def offer(iri, rank, module, via, matched):
            if iri not in best or rank < best[iri][0]:
                best[iri] = (rank, module, via, matched)

        for module, quad in self.quads():
            subject = quad.subject
            if not isinstance(subject, NamedNode):
                continue
            iri = subject.value
            if needle in local_name(iri).lower():
                offer(iri, 3, module, "iri", iri)
            obj = quad.object
            if not isinstance(obj, Literal):
                continue
            p = quad.predicate.value
            literal = obj.value.lower()
            if p in LABELS:
                if literal == needle:
                    offer(iri, 0, module, local_name(p), obj.value)
                elif literal.startswith(needle):
                    offer(iri, 1, module, local_name(p), obj.value)
                elif needle in literal:
                    offer(iri, 2, module, local_name(p), obj.value)
            elif p in DEFINITIONS and needle in literal:
                offer(iri, 4, module, local_name(p), obj.value)
        hits = []
        for iri, (rank, module, via, matched) in sorted(best.items(), key=lambda kv: (kv[1][0], self.curie(kv[0]))):
            hit = {"iri": iri, "curie": self.curie(iri), "module": module.id, "via": via, "text": matched[:140]}
            if module.layer:
                hit["layer"] = module.layer
            hits.append(hit)
            if len(hits) >= limit:
                break
        return hits

    # ---- layer chain ---------------------------------------------------------------------------------------
    def layer_chain(self, iri: str) -> dict:
        chain, seen, current, last_module = [], set(), iri, None
        while current and current not in seen and len(chain) < MAX_CHAIN:
            seen.add(current)
            d = self.describe(current)
            if d is None:
                chain.append({"term": self.curie(current)})
                last_module = None
                break
            module = (d["_declaredIn"] or d["definedIn"])[0]
            entry = {"term": d["curie"], "module": module, "layer": d.get("layer"), "linkStatus": d.get("linkStatus")}
            chain.append({k: v for k, v in entry.items() if v is not None})
            last_module = module
            parents = sorted(d["parents"])
            current = self.expand(parents[0]) if parents else None
        layers = {m.id: m.layer for m in self.modules}
        return {"chain": chain, "endsAtFoundational": layers.get(last_module) == "Foundational"}

    async def find(self, q: str | None, limit: int, include_unpublished: bool) -> dict:
        """GET /knowledgeTerm?q=: ranked search."""
        if q is None or not q.strip():
            raise ApiError(400, "400", "Bad Request", "q is required and must not be blank")
        if not 1 <= limit <= 1000:
            raise ApiError(400, "400", "Bad Request", "limit must be between 1 and 1000")
        await self.load(include_unpublished)
        return {"hits": self.search(q, limit)}

    async def lookup(self, iri: str | None, layer_chain: bool, include_unpublished: bool) -> dict:
        """GET /knowledgeTerm/describe?iri=: one term, and its layer chain when asked."""
        if not iri:
            raise ApiError(400, "400", "Bad Request", "iri is required")
        await self.load(include_unpublished)
        full = self.expand(iri)
        description = self.describe(full)
        if description is None:
            raise ApiError(404, "404", "Not Found", f"No module states anything about '{iri}'")
        description.pop("_declaredIn")
        out = {"description": description}
        if layer_chain:
            out["layerChain"] = self.layer_chain(full)
        return out
