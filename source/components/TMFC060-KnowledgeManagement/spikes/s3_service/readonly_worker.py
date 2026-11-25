"""Spike S3: can a separate (killable) process query the store while the API process holds it for writing?

Run: python readonly_worker.py. Creates a store with 200k triples, then opens it read-only from a child
process, twice: before and after one more write.
"""
import shutil, subprocess, sys, tempfile
from pyoxigraph import Store, NamedNode, Literal, Quad

path = tempfile.mkdtemp(prefix="s3store-")
shutil.rmtree(path)
writer = Store(path)
writer.bulk_extend([Quad(NamedNode(f"urn:s{i}"), NamedNode("urn:p"), Literal(str(i)), NamedNode("urn:g"))
                    for i in range(200000)])
writer.flush()
child = f"""
import time
t = time.time()
from pyoxigraph import Store
reader = Store.read_only({path!r}); t1 = time.time()
n = next(iter(reader.query('SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ ?s ?p ?o }} }}')))['n'].value
print(f'open {{(t1 - t) * 1000:.0f}} ms, count {{n}}')
"""
print("before:", subprocess.run([sys.executable, "-c", child], capture_output=True, text=True).stdout.strip())
writer.add(Quad(NamedNode("urn:new"), NamedNode("urn:p"), Literal("x"), NamedNode("urn:g")))
writer.flush()
print("after a new write:", subprocess.run([sys.executable, "-c", child], capture_output=True, text=True).stdout.strip())
shutil.rmtree(path, ignore_errors=True)
