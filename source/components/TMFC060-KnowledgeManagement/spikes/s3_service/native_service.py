"""Spike S3 (hw-irc-sni/oda-canvas#20): does pyoxigraph execute SPARQL SERVICE, and can it be allowlisted?

Run: python spike_s3.py <peer-query-url>, with a SPARQL endpoint at that URL holding
<https://partner.example.net/cell/4711> <https://partner.example.net/ont#vendor> "VendorA" (and cell 4712).
"""
import sys, time
import pyoxigraph
from pyoxigraph import Store, NamedNode, Literal, Quad

PEER = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:17878/query"
ONLY = sys.argv[2] if len(sys.argv) > 2 else None
print("pyoxigraph", pyoxigraph.__version__)
store = Store()  # in-memory; the RocksDB-backed Store(path) uses the same query engine
store.add(Quad(NamedNode("https://example.org/incident/INC-1042"),
               NamedNode("https://example.org/ont#affects"),
               NamedNode("https://partner.example.net/cell/4711")))

def run(label, query, **kw):
    if ONLY and not label.startswith(ONLY):
        return
    t = time.time()
    try:
        res = store.query(query, **kw)
        rows = [[(s[v].value if s[v] is not None else None) for v in res.variables] for s in res]
        print(f"[{label}] OK {time.time()-t:.2f}s rows={rows}")
    except Exception as e:
        print(f"[{label}] {type(e).__name__} {time.time()-t:.2f}s: {e}")

# (a) plain SERVICE
run("a1 plain SERVICE", f"SELECT ?s ?v WHERE {{ SERVICE <{PEER}> {{ ?s <https://partner.example.net/ont#vendor> ?v }} }}")
# (a) SERVICE joined with local data
run("a2 join local+SERVICE", f"""SELECT ?inc ?v WHERE {{
  ?inc <https://example.org/ont#affects> ?cell .
  SERVICE <{PEER}> {{ ?cell <https://partner.example.net/ont#vendor> ?v }} }}""")
# unreachable endpoint, with and without SILENT
run("b1 unreachable", "SELECT * WHERE { SERVICE <http://10.255.255.1:8080/query> { ?s ?p ?o } }")
run("b2 unreachable SILENT", "SELECT * WHERE { SERVICE SILENT <http://10.255.255.1:8080/query> { ?s ?p ?o } }")
# variable SERVICE IRI
run("b3 variable SERVICE", "SELECT * WHERE { VALUES ?e { <%s> } SERVICE ?e { ?s ?p ?o } }" % PEER)
# (b) does any query option block SERVICE? default_graph / named_graphs restrict the local dataset only
run("b4 with default_graph restriction", f"SELECT ?s WHERE {{ SERVICE <{PEER}> {{ ?s ?p ?o }} }}",
    default_graph=[NamedNode("urn:tmfc060:kb:none")], named_graphs=[])
