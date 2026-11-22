"""Spike S3 fallback: find every SERVICE IRI of a SPARQL query with rdflib, before pyoxigraph runs it."""
import rdflib
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.term import Variable, URIRef

def service_iris(query: str):
    """Return the SERVICE targets of a query: IRIs as str, variables as Variable."""
    found = []
    def walk(node):
        if hasattr(node, "name") and node.name == "ServiceGraphPattern":
            found.append(node.term)
        if isinstance(node, dict):
            for v in node.values(): walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node: walk(v)
    walk(translateQuery(parseQuery(query)).algebra)
    return found

PEER = "http://localhost:17878/query"
cases = {
  "plain": f"SELECT * WHERE {{ SERVICE <{PEER}> {{ ?s ?p ?o }} }}",
  "in OPTIONAL": f"SELECT * WHERE {{ ?a ?b ?c OPTIONAL {{ SERVICE <http://evil.example/sparql> {{ ?s ?p ?o }} }} }}",
  "in UNION": f"SELECT * WHERE {{ {{ ?a ?b ?c }} UNION {{ SERVICE SILENT <http://evil.example/sparql> {{ ?s ?p ?o }} }} }}",
  "in subquery": f"SELECT * WHERE {{ {{ SELECT ?s WHERE {{ SERVICE <http://evil.example/sparql> {{ ?s ?p ?o }} }} }} }}",
  "nested SERVICE": f"SELECT * WHERE {{ SERVICE <{PEER}> {{ SERVICE <http://evil.example/sparql> {{ ?s ?p ?o }} }} }}",
  "variable": "SELECT * WHERE { SERVICE ?e { ?s ?p ?o } }",
  "in ASK": "ASK { SERVICE <http://evil.example/sparql> { ?s ?p ?o } }",
  "in CONSTRUCT": "CONSTRUCT { ?s ?p ?o } WHERE { SERVICE <http://evil.example/sparql> { ?s ?p ?o } }",
  "none": "SELECT * WHERE { ?s ?p ?o }",
}
ALLOW = {PEER}
print("rdflib", rdflib.__version__)
for name, q in cases.items():
    try:
        targets = service_iris(q)
    except Exception as e:
        print(f"{name:15} parse error {type(e).__name__}: {str(e).splitlines()[0][:100]} -> REJECT (fail closed)")
        continue
    bad = [t for t in targets if isinstance(t, Variable) or str(t) not in ALLOW]
    print(f"{name:15} targets={[str(t) for t in targets]} -> {'REJECT ' + str([str(b) for b in bad]) if bad else 'allow'}")
