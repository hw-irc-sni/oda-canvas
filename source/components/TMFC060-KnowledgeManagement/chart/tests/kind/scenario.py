"""TMFC060 on a kind Canvas: smoke checks of the deployed Component and the PHASE 2 pipeline scenario (#50) through the
Canvas gateway, with real Keycloak tokens and the model route the release is configured with.

Run by run.sh, which installs the release and port-forwards Keycloak. Standard library only. Settings (environment):
  RELEASE, NAMESPACE        the Helm release and its namespace (the Component is <RELEASE>-knowledgemanagement)
  GATEWAY                   the Canvas gateway base URL (run.sh port-forwards the Istio ingress; its certificate is
                            not checked)
  KC_URL, KC_HOST           Keycloak through the port-forward, and the in-cluster host:port the tokens must name as issuer
  KC_ADMIN_USER, KC_ADMIN_PASSWORD   Keycloak admin (master realm); never printed
  EXPECT_ROUTE              the model route the API must have selected (agentgateway, openaiCompatible, ...)
  FIXTURES                  the #50 fixtures (pipelineWorkerMicroservice/tests/fixtures)
The scenario creates one temporary user holding the five TMFC060 roles and deletes it at the end.
"""
import base64
import json
import os
import secrets
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

RELEASE = os.environ["RELEASE"]
NAMESPACE = os.environ.get("NAMESPACE", "components")
COMPONENT = f"{RELEASE}-knowledgemanagement"
GATEWAY = os.environ.get("GATEWAY", "https://127.0.0.1").rstrip("/")
KC_URL = os.environ["KC_URL"].rstrip("/")
KC_HOST = os.environ["KC_HOST"]
EXPECT_ROUTE = os.environ.get("EXPECT_ROUTE", "agentgateway")
FIXTURES = Path(os.environ["FIXTURES"])
REALM = "odari"
ROLES = ["knowledge-viewer", "knowledge-editor", "ontology-editor", "ontology-reviewer", "ontology-approver"]
BASE = f"{GATEWAY}/{COMPONENT}/tmf-api/knowledgeManagement/v5"
PROCESS = f"{GATEWAY}/{COMPONENT}/tmf-api/process/v5"
AS = "https://example.org/e2e/assurance#"
ICM = "http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/"
HB = "https://example.org/e2e/intent/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
PROV = "http://www.w3.org/ns/prov#"
INSECURE = ssl.create_default_context()
INSECURE.check_hostname, INSECURE.verify_mode = False, ssl.CERT_NONE

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'' if ok or not detail else ': ' + detail[:600]}", flush=True)
    if not ok:
        failures.append(name)
    return ok


def http(method: str, url: str, body=None, headers=None, timeout=60, host: str | None = None):
    """(status, headers, parsed JSON or text); body: dict -> JSON, bytes as is, str -> form-encoded already."""
    data, hdrs = None, dict(headers or {})
    if isinstance(body, dict) or isinstance(body, list):
        data, hdrs["Content-Type"] = json.dumps(body).encode(), hdrs.get("Content-Type", "application/json")
    elif isinstance(body, bytes):
        data = body
    elif isinstance(body, str):
        data, hdrs["Content-Type"] = body.encode(), "application/x-www-form-urlencoded"
    if host:
        hdrs["Host"] = host
    request = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=INSECURE) as r:
            raw, status, rh = r.read(), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw, status, rh = e.read(), e.code, dict(e.headers)
    text = raw.decode("utf-8", "replace")
    try:
        return status, rh, json.loads(text) if text else None
    except ValueError:
        return status, rh, text


# ---- Keycloak ---------------------------------------------------------------------------------------------------
def admin_headers() -> dict:
    """A fresh master-realm admin token per call (they live about a minute)."""
    form = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                   "username": os.environ["KC_ADMIN_USER"], "password": os.environ["KC_ADMIN_PASSWORD"]})
    status, _, body = http("POST", f"{KC_URL}/realms/master/protocol/openid-connect/token", form)
    if status != 200:
        raise SystemExit(f"Keycloak admin login failed (HTTP {status})")
    return {"Authorization": f"Bearer {body['access_token']}"}


def admin(method: str, path: str, body=None):
    return http(method, f"{KC_URL}/admin/realms/{REALM}{path}", body, admin_headers())


class User:
    """A temporary realm user with the five TMFC060 client roles; tokens by password grant on canvas-portal, requested
    with the in-cluster Keycloak host so that their issuer is the one the API accepts."""

    def __init__(self):
        self.name, self.password, self.id = f"{RELEASE}-scenario-{secrets.token_hex(3)}", secrets.token_urlsafe(18), None
        self.token, self.fetched = None, 0.0

    def create(self, client_uuid: str) -> None:
        status, _, _ = admin("POST", "/users", {"username": self.name, "enabled": True, "emailVerified": True,
                                                "firstName": "TMFC060", "lastName": "Scenario",
                                                "email": f"{self.name}@example.org",
                                                "credentials": [{"type": "password", "value": self.password,
                                                                 "temporary": False}]})
        if status != 201:
            raise SystemExit(f"creating the scenario user failed (HTTP {status})")
        self.id = admin("GET", f"/users?username={self.name}&exact=true")[2][0]["id"]
        roles = [admin("GET", f"/clients/{client_uuid}/roles/{r}")[2] for r in ROLES]
        status, _, _ = admin("POST", f"/users/{self.id}/role-mappings/clients/{client_uuid}", roles)
        if status != 204:
            raise SystemExit(f"granting the TMFC060 roles failed (HTTP {status})")

    def delete(self) -> None:
        if self.id:
            admin("DELETE", f"/users/{self.id}")

    def headers(self) -> dict:
        if not self.token or time.time() - self.fetched > 45:
            form = urllib.parse.urlencode({"grant_type": "password", "client_id": "canvas-portal",
                                           "username": self.name, "password": self.password})
            status, _, body = http("POST", f"{KC_URL}/realms/{REALM}/protocol/openid-connect/token", form, host=KC_HOST)
            if status != 200:
                raise SystemExit(f"user login failed (HTTP {status})")
            self.token, self.fetched = body["access_token"], time.time()
        return {"Authorization": f"Bearer {self.token}"}


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def kubectl(*args: str) -> str:
    return subprocess.run(["kubectl", *args], check=True, capture_output=True, text=True).stdout


# ---- smoke ------------------------------------------------------------------------------------------------------
def smoke(user: User, client_uuid: str) -> None:
    status = json.loads(kubectl("get", "components", "-n", NAMESPACE, COMPONENT, "-o", "json"))["status"]
    check("Component deployment status Complete", status["summary/status"]["deployment_status"] == "Complete",
          json.dumps(status["summary/status"]))
    apis = json.loads(kubectl("get", "exposedapis", "-n", NAMESPACE, "-l",
                              f"oda.tmforum.org/componentName={COMPONENT}", "-o", "json"))["items"]
    ready = {a["spec"]["name"]: (a.get("status", {}).get("implementation", {}).get("ready"),
                                 a.get("status", {}).get("apiStatus", {}).get("url")) for a in apis}
    check("ExposedAPIs knowledgemanagement, processflow, knowledgemcp and metrics ready",
          set(ready) == {"knowledgemanagement", "processflow", "knowledgemcp", "metrics"}
          and all(r for r, _ in ready.values()), json.dumps(ready))

    roles = {r["name"] for r in admin("GET", f"/clients/{client_uuid}/roles")[2]}
    check("Keycloak client roles: the five static roles and canvasRole", set(ROLES) | {"canvasRole"} <= roles,
          str(sorted(roles)))
    c = claims(user.headers()["Authorization"].split()[1])
    check("token issuer and audience the API accepts",
          c["iss"] == f"http://{KC_HOST}/auth/realms/{REALM}" and COMPONENT in (c["aud"] if isinstance(c["aud"], list)
                                                                                 else [c["aud"]])
          and set(ROLES) <= set(c.get("resource_access", {}).get(COMPONENT, {}).get("roles", [])),
          json.dumps({k: c.get(k) for k in ("iss", "aud", "azp")}))
    lifespan = admin("GET", "")[2].get("accessTokenLifespan")
    print(f"info realm accessTokenLifespan {lifespan} s (tokens are refreshed every 45 s)", flush=True)

    s, _, body = http("GET", f"{BASE}/openapi.yaml")
    check("knowledgemanagement OAS through the gateway, no token", s == 200 and "Knowledge Management" in str(body), str(s))
    s, _, _ = http("GET", f"{BASE}/knowledge")
    check("knowledgemanagement refuses a call without a token", s == 401, str(s))
    s, _, body = http("GET", f"{BASE}/knowledge", headers=user.headers())
    check("knowledgemanagement /knowledge with a token", s == 200 and isinstance(body, list), f"{s} {body}")
    s, _, body = http("GET", f"{PROCESS}/process", headers=user.headers())
    check("processflow (TMF701) /process with a token", s == 200 and isinstance(body, list), f"{s} {body}")
    s, _, body = http("GET", f"{PROCESS}/openapi.yaml")
    check("processflow OAS through the gateway", s == 200, str(s))
    s, _, body = http("GET", f"{GATEWAY}/{COMPONENT}/metrics")
    check("metrics through the gateway", s == 200 and "tmfc060_http_requests_total" in str(body), str(s))

    mcp = f"{GATEWAY}/{COMPONENT}/mcp"
    accept = {"Accept": "application/json, text/event-stream"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "tmfc060-kind", "version": "1"}}}
    s, _, _ = http("POST", mcp, init, accept)
    check("knowledgemcp refuses a call without a token", s == 401, str(s))
    s, _, body = http("POST", mcp, init, {**accept, **user.headers()})
    check("knowledgemcp initialize with a token", s == 200 and "serverInfo" in json.dumps(body), f"{s} {body}")
    s, _, body = http("POST", mcp, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, {**accept, **user.headers()})
    tools = [t["name"] for t in (body or {}).get("result", {}).get("tools", [])] if isinstance(body, dict) else []
    check("knowledgemcp tools/list", s == 200 and "hybrid_search" in tools, f"{s} {tools}")


# ---- the pipeline -----------------------------------------------------------------------------------------------
def api(user: User, method: str, path: str, body=None, headers=None, timeout=120):
    return http(method, f"{BASE}{path}", body, {**user.headers(), **(headers or {})}, timeout)


def multipart(name: str, content: bytes, media_type: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: {media_type}\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def ask(user: User, kb: str, sparql: str) -> dict:
    s, _, body = api(user, "POST", f"/knowledge/{kb}/query", sparql.encode(),
                     {"Content-Type": "application/sparql-query", "Accept": "application/sparql-results+json"})
    if s != 200:
        raise AssertionError(f"query failed: {s} {body}")
    return body


def wait_done(user: User, pid: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s, _, p = http("GET", f"{PROCESS}/process/{pid}", headers=user.headers())
        if s == 200 and p["state"] in ("completed", "failed"):
            return p
        time.sleep(5)
    raise AssertionError(f"process {pid} not finished after {timeout} s")


def pipeline(user: User) -> None:
    kb = f"harbour-{secrets.token_hex(2)}"
    # the e2e ontology and its shapes, published through the governance workflow (validation on)
    for mid, kind, fixture, iri in (
            ("e2e-as", "ontology", "harbour-ontology.ttl", "https://example.org/e2e/assurance"),
            ("e2e-shapes", "shapes", "harbour-shapes.ttl", "https://example.org/e2e/assurance-shapes")):
        s, _, body = api(user, "POST", "/knowledgeSpecification", {
            "id": mid, "name": mid, "iri": iri, "layer": "Telecom", "specKind": kind, "source": {"kind": "upload"}})
        check(f"module {mid} created", s == 201, f"{s} {body}")
        s, _, body = api(user, "PUT", f"/knowledgeSpecification/{mid}/content", (FIXTURES / fixture).read_bytes(),
                         {"Content-Type": "text/turtle"})
        check(f"module {mid} content", s < 300, f"{s} {body}")
        for status in ("proposed", "inReview", "agreed", "published"):
            s, _, body = api(user, "PATCH", f"/knowledgeSpecification/{mid}", {"lifecycleStatus": status})
            if not check(f"module {mid} {status}", s == 200, f"{s} {body}"):
                break

    s, _, body = api(user, "POST", "/knowledge", {
        "id": kb, "name": kb, "storeKind": "hybrid", "conformsTo": [{"id": "e2e-as"}, {"id": "e2e-shapes"}],
        "source": [{"kind": "upload"}, {"kind": "tmf921"}]})
    check(f"knowledge-base {kb} created", s == 201, f"{s} {body}")
    upload, ctype = multipart("harbour-runbook.md", (FIXTURES / "harbour-runbook.md").read_bytes(), "text/markdown")
    s, _, body = api(user, "POST", f"/knowledge/{kb}/document", upload, {"Content-Type": ctype})
    check("runbook uploaded", s == 201, f"{s} {body}")
    doc = body["id"]
    intent = json.loads((FIXTURES / "harbour-intent.json").read_text())
    processes = {}
    for label, ingest in (("runbook", {"source": "upload", "documentId": [doc]}),
                          ("intent", {"source": "tmf921", "params": {"resources": [intent]}})):
        s, _, body = api(user, "POST", f"/knowledge/{kb}/ingest", ingest)
        check(f"{label} ingest started", s == 201, f"{s} {body}")
        started = time.time()
        processes[label] = wait_done(user, body["id"], timeout=1800)
        print(f"info {label} ingest finished in {time.time() - started:.0f} s", flush=True)

    # deterministic: TMF701 processes and their KM-FX-01..06 tasks, direct lifting, provenance, chunks
    for label, p in processes.items():
        tasks = [http("GET", f"{PROCESS}/task/{t['id']}", headers=user.headers())[2] for t in p["task"]]
        check(f"{label} process completed, tasks KM-FX-01..06 completed",
              p["state"] == "completed" and [t["step"] for t in tasks] == [f"KM-FX-0{n}" for n in range(1, 7)]
              and {t["state"] for t in tasks} == {"completed"},
              json.dumps({"state": p["state"], "tasks": [(t.get("step"), t.get("state"), t.get("errorMessage"))
                                                         for t in tasks]}))
    for s_, p_, o_ in ((HB + "INT-HARB-7", RDF_TYPE, ICM + "Intent"),
                       (HB + "INT-HARB-7", ICM + "hasExpectation", HB + "INT-HARB-7-latency"),
                       (HB + "INT-HARB-7-latency", ICM + "target", HB + "HarbourDistrict")):
        check(f"intent lifted: <{p_.rsplit('/', 1)[-1].rsplit('#', 1)[-1]}>",
              ask(user, kb, f"ASK {{ <{s_}> <{p_}> <{o_}> }}")["boolean"])
    rows = ask(user, kb, f"SELECT ?e ?src WHERE {{ ?e <{PROV}wasDerivedFrom> ?src }} LIMIT 5")["results"]["bindings"]
    check("PROV-O provenance for derived entities", bool(rows))
    s, _, body = api(user, "GET", f"/knowledge/{kb}")
    stats = body.get("statistics", {}) if s == 200 else {}
    check("statistics: 1 document, chunks and triples", stats.get("documents") == 1 and stats.get("chunks", 0) > 0
          and stats.get("triples", 0) > 0, json.dumps(stats))
    s, _, body = api(user, "POST", f"/knowledge/{kb}/search", {"query": "intent latency harbour", "topK": 5,
                                                                "iriFilter": [intent["href"]]})
    check("the intent's chunk is tagged with the intent href", s == 200 and intent["href"] in
          {i for h in body.get("hits", []) for i in h.get("entityIri", [])}, f"{s} {json.dumps(body)[:400]}")
    health = json.loads(kubectl("exec", "-n", NAMESPACE, f"{RELEASE}-kmapi-0", "-c", "api", "--", "python", "-c",
                                "import json,urllib.request;print(urllib.request.urlopen("
                                "'http://127.0.0.1:8080/health').read().decode())"))
    route = health["checks"]["modelRoute"]
    check(f"model route {EXPECT_ROUTE}", route.get("route") == EXPECT_ROUTE,
          json.dumps({k: route.get(k) for k in ("route", "chatModel", "embeddingModel", "embeddingDimension")}))

    # model-dependent: structure only
    rows = ask(user, kb, f"SELECT ?e ?c WHERE {{ ?e a ?c FILTER(STRSTARTS(STR(?c), '{AS}')) }} LIMIT 20")
    typed = rows["results"]["bindings"]
    check("at least one entity typed by an e2e-as class", bool(typed))
    print("info typed entities: " + ", ".join(sorted({b['c']['value'].rsplit('#', 1)[-1] for b in typed})), flush=True)
    s, _, body = api(user, "POST", f"/knowledge/{kb}/search", {"query": "cell outage power alarm", "topK": 5})
    hits = body.get("hits", []) if s == 200 else []
    check("runbook search: hits carry entityIri, the top hit is from the runbook",
          bool(hits) and all("entityIri" in h for h in hits) and hits[0].get("documentId") == doc,
          f"{s} {json.dumps(body)[:400]}")

    # tidy up the scenario's data (the release is removed by run.sh)
    api(user, "DELETE", f"/knowledge/{kb}")


def main() -> int:
    clients = admin("GET", f"/clients?clientId={COMPONENT}")[2]
    if not clients:
        print(f"FAIL the Keycloak client {COMPONENT} does not exist")
        return 1
    user = User()
    try:
        user.create(clients[0]["id"])
        smoke(user, clients[0]["id"])
        if not failures:
            pipeline(user)
    finally:
        user.delete()
    print(f"{len(failures)} check(s) failed: {failures}" if failures else "all kind checks passed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
