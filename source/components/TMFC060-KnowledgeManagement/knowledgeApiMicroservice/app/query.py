"""Read-only SPARQL over a knowledge-base dataset (spike S3, hw-irc-sni/oda-canvas#20).

1. Pre-check with rdflib: the query must parse, must not be an update, and every SERVICE target must be an
   allowlisted endpoint (403 SERVICE_NOT_ALLOWED otherwise; variable targets are always refused).
2. The query runs in a freshly spawned process that opens the store read-only, so it can be killed at the
   timeout (504) while the API process stays the only writer.
3. SELECT results are capped at a row limit and CONSTRUCT/DESCRIBE results at a triple limit; ``truncated`` says
   whether the cap applied.
"""
import asyncio
import multiprocessing
from dataclasses import dataclass

from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.term import Variable

from .errors import ApiError

_spawn = multiprocessing.get_context("spawn")


@dataclass
class QueryResult:
    kind: str  # "select", "ask" or "graph"
    body: object  # SPARQL JSON results dict (select/ask) or serialized RDF bytes (graph)
    truncated: bool = False


class QueryTimeout(ApiError):
    def __init__(self, seconds: int):
        super().__init__(504, "504", "Gateway Timeout", f"The query did not finish within {seconds} s")


def service_targets(query: str) -> list:
    """The SERVICE targets of a query (IRIs as str, variables as Variable). Raises on a parse failure."""
    found = []

    def walk(node):
        if getattr(node, "name", None) == "ServiceGraphPattern":
            found.append(node.term)
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(translateQuery(parseQuery(query)).algebra)
    return found


def precheck(query: str, allowed_services: frozenset[str] = frozenset()) -> None:
    try:
        targets = service_targets(query)
    except RecursionError as exc:  # rdflib cannot parse SERVICE nested in SERVICE: refuse it (fail closed)
        raise ApiError(403, "SERVICE_NOT_ALLOWED", "SERVICE endpoint not allowed",
                       "Nested SERVICE clauses are not supported") from exc
    except Exception as exc:  # noqa: BLE001 - any parse failure, including an update, is a bad request
        raise ApiError(400, "400", "Bad Request", f"Not a valid read-only SPARQL query: {exc}") from exc
    refused = [str(t) for t in targets if isinstance(t, Variable) or str(t) not in allowed_services]
    if refused:
        raise ApiError(403, "SERVICE_NOT_ALLOWED", "SERVICE endpoint not allowed",
                       f"Not an allowlisted peer: {', '.join(refused)}")


def _term_json(term) -> dict:
    from pyoxigraph import BlankNode, Literal, NamedNode, Triple
    if isinstance(term, NamedNode):
        return {"type": "uri", "value": term.value}
    if isinstance(term, BlankNode):
        return {"type": "bnode", "value": term.value}
    if isinstance(term, Literal):
        out = {"type": "literal", "value": term.value}
        if term.language:
            out["xml:lang"] = term.language
        elif term.datatype.value != "http://www.w3.org/2001/XMLSchema#string":
            out["datatype"] = term.datatype.value
        return out
    if isinstance(term, Triple):
        return {"type": "triple", "value": {"subject": _term_json(term.subject),
                                            "predicate": _term_json(term.predicate),
                                            "object": _term_json(term.object)}}
    raise TypeError(f"unexpected term {term!r}")


def _worker(conn, store_path, query, default_graph, row_cap, graph_format):
    """Runs in a spawned process: open the store read-only, run the query, send the result back."""
    try:
        from pyoxigraph import NamedNode, QueryBoolean, QuerySolutions, RdfFormat, Store, serialize
        store = Store.read_only(store_path)
        graphs = [NamedNode(g) for g in default_graph]
        result = store.query(query, default_graph=graphs, named_graphs=graphs)
        if isinstance(result, QueryBoolean):
            conn.send(("ok", "ask", {"head": {}, "boolean": bool(result)}, False))
        elif isinstance(result, QuerySolutions):
            variables = [v.value for v in result.variables]
            rows, truncated = [], False
            for solution in result:
                if len(rows) >= row_cap:
                    truncated = True
                    break
                rows.append({v: _term_json(solution[v]) for v in variables if solution[v] is not None})
            conn.send(("ok", "select", {"head": {"vars": variables}, "results": {"bindings": rows}}, truncated))
        else:
            triples, truncated = [], False
            for triple in result:
                if len(triples) >= row_cap:
                    truncated = True
                    break
                triples.append(triple)
            fmt = RdfFormat.JSON_LD if graph_format == "application/ld+json" else RdfFormat.TURTLE
            conn.send(("ok", "graph", serialize(triples, format=fmt), truncated))
    except SyntaxError as exc:
        conn.send(("error", 400, f"Invalid query: {exc}"))
    except Exception as exc:  # noqa: BLE001 - reported to the parent as a 500
        conn.send(("error", 500, f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


async def run_query(store_path: str, query: str, default_graph: list[str], *, timeout: int, row_cap: int,
                    allowed_services: frozenset[str] = frozenset(),
                    graph_format: str = "text/turtle") -> QueryResult:
    precheck(query, allowed_services)
    parent, child = _spawn.Pipe(duplex=False)
    process = _spawn.Process(target=_worker, args=(child, store_path, query, default_graph, row_cap, graph_format),
                             daemon=True)
    process.start()
    child.close()
    try:
        ready = await asyncio.to_thread(parent.poll, timeout)
        if not ready:
            raise QueryTimeout(timeout)
        message = await asyncio.to_thread(parent.recv)
    except EOFError as exc:
        raise ApiError(500, "500", "Internal Server Error", "The query worker exited without a result") from exc
    finally:
        if process.is_alive():
            process.kill()
        await asyncio.to_thread(process.join, 5)
        parent.close()
    if message[0] == "error":
        _, status, text = message
        raise ApiError(status, str(status), "Bad Request" if status == 400 else "Internal Server Error", text)
    _, kind, body, truncated = message
    return QueryResult(kind=kind, body=body, truncated=truncated)


def effective_timeout(requested: int | None, default: int, maximum: int) -> int:
    """The client's timeout, defaulted and capped by the server settings."""
    return max(1, min(requested or default, maximum))
