#!/usr/bin/env bash
# Offline checks of the TMFC060 chart: helm lint, renders that must succeed (with what they must contain) and
# values that must be refused. Needs helm and python3 with PyYAML. Run from anywhere: chart/tests/check.sh
set -euo pipefail
CHART="$(cd "$(dirname "$0")/.." && pwd)"
failures=0
pass() { echo "ok   $1"; }
fail() { echo "FAIL $1"; failures=$((failures + 1)); }

# Until the chart has its own Qdrant (#54) every render needs an external one; NO_STORES=1 leaves it out
STORES=(--set qdrant.external.url=http://qdrant:6333)
render() {
  if [ "${NO_STORES:-}" = 1 ]; then helm template km "$CHART" -n components "$@"
  else helm template km "$CHART" -n components "${STORES[@]}" "$@"; fi
}

# expect NAME PYTHON-EXPR [helm args...]: the render succeeds and EXPR holds; docs = the rendered objects,
# kind(k) = the objects of kind k, comp = the Component spec
expect() {
  local name="$1" expr="$2"; shift 2
  local out
  if ! out="$(render "$@" 2>&1)"; then fail "$name: render failed: $out"; return; fi
  if EXPR="$expr" python3 -c '
import os, sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
kind = lambda k: [d for d in docs if d["kind"] == k]
comp = kind("Component")[0]["spec"]
one = lambda k, n: next(d for d in docs if d["kind"] == k and d["metadata"]["name"] == n)
pod = lambda k, n: one(k, n)["spec"]["template"]["spec"]
env = lambda k, n: {e["name"]: e.get("value", e.get("valueFrom")) for e in pod(k, n)["containers"][0]["env"]}
svcports = lambda n: {p["port"] for p in one("Service", n)["spec"]["ports"]}
sys.exit(0 if eval(os.environ["EXPR"]) else 1)' <<<"$out"; then pass "$name"; else fail "$name: $expr"; fi
}

# refuse NAME MESSAGE-FRAGMENT [helm args...]: the render fails and its error mentions the fragment
refuse() {
  local name="$1" fragment="$2"; shift 2
  local out
  if out="$(render "$@" 2>&1)"; then fail "$name: render succeeded"; return; fi
  if grep -qF -- "$fragment" <<<"$out"; then pass "$name"; else fail "$name: no '$fragment' in: $out"; fi
}

ALL_DEPS=()
for d in tmf639 tmf638 tmf642 tmf628 tmf921 aimanagement eventmanagement partymanagement partyrolemanagement; do
  ALL_DEPS+=(--set "dependentAPIs.$d.enabled=true")
done

if helm lint "$CHART" "${STORES[@]}" >/dev/null; then pass "helm lint"; else fail "helm lint"; helm lint "$CHART" "${STORES[@]}" || true; fi

# Component (#51)
expect "component name, labels and id" \
  'kind("Component")[0]["metadata"]["name"] == "km-knowledgemanagement" and kind("Component")[0]["metadata"]["labels"]["oda.tmforum.org/componentId"] == "TMFC060" and comp["componentMetadata"]["id"] == "TMFC060"'
expect "exposed APIs and their paths" \
  '[(a["name"], a["apiType"], a["path"]) for a in comp["coreFunction"]["exposedAPIs"]] == [("knowledgemanagement", "openapi", "/km-knowledgemanagement/tmf-api/knowledgeManagement/v5"), ("processflow", "openapi", "/km-knowledgemanagement/tmf-api/process/v5"), ("knowledgemcp", "mcp", "/km-knowledgemanagement/mcp")]'
expect "spec URLs default to the OAS the API serves" \
  'comp["coreFunction"]["exposedAPIs"][0]["specification"][0]["url"] == "http://km-kmapi.components.svc.cluster.local:8080/km-knowledgemanagement/tmf-api/knowledgeManagement/v5/openapi.yaml"'
expect "metrics management API" \
  'comp["managementFunction"]["exposedAPIs"] == [{"name": "metrics", "apiType": "prometheus", "implementation": "km-kmapi", "path": "/km-knowledgemanagement/metrics", "port": 4000}]'
expect "static roles, no TMF669" \
  '[r["name"] for r in comp["securityFunction"]["componentRole"]] == ["knowledge-viewer", "knowledge-editor", "ontology-editor", "ontology-reviewer", "ontology-approver"] and comp["securityFunction"]["exposedAPIs"] == []'
expect "no dependent APIs or subscribed events by default" \
  'comp["coreFunction"]["dependentAPIs"] == [] and comp["eventNotification"]["subscribedEvents"] == []'
expect "enabled dependent APIs are optional, with their events" \
  'sorted(d["name"] for d in comp["coreFunction"]["dependentAPIs"]) == ["aimanagement", "eventmanagement", "partymanagement", "partyrolemanagement", "tmf628", "tmf638", "tmf639", "tmf642", "tmf921"] and all(d["required"] is False for d in comp["coreFunction"]["dependentAPIs"]) and "IntentCreateEvent" in [e["name"] for e in comp["eventNotification"]["subscribedEvents"]]' \
  "${ALL_DEPS[@]}"
expect "TMF723 with a URL" \
  '[d["id"] for d in comp["coreFunction"]["dependentAPIs"]] == ["TMF723"]' \
  --set dependentAPIs.policymanagement.enabled=true --set dependentAPIs.policymanagement.specification=https://example.org/tmf723.json
refuse "TMF723 enabled without a URL" "policymanagement.specification" --set dependentAPIs.policymanagement.enabled=true
refuse "TMF723 without a URL, schema skipped" "has no specification URL" \
  --skip-schema-validation --set dependentAPIs.policymanagement.enabled=true
refuse "invalid component name" "component.name" --set component.name=Bad_Name
refuse "unknown component key" "Additional property colour" --set component.colour=blue

# Workloads (#52)
expect "API StatefulSet with one replica and a store PVC, worker and MCP Deployments" \
  'one("StatefulSet", "km-kmapi")["spec"]["replicas"] == 1 and one("StatefulSet", "km-kmapi")["spec"]["volumeClaimTemplates"][0]["spec"]["accessModes"] == ["ReadWriteOnce"] and {d["metadata"]["name"] for d in kind("Deployment")} == {"km-kmworker", "km-kmmcp"}'
expect "COMPONENT_NAME is the Component name (the token audience)" \
  'env("StatefulSet", "km-kmapi")["COMPONENT_NAME"] == kind("Component")[0]["metadata"]["name"]'
expect "the Services the ExposedAPIs name exist with their ports" \
  'all(a["port"] in svcports(a["implementation"]) for a in comp["coreFunction"]["exposedAPIs"] + comp["managementFunction"]["exposedAPIs"])'
expect "workers call the internal Service; MCP serves the ExposedAPI path and calls the API" \
  'env("Deployment", "km-kmworker")["INTERNAL_API_URL"] == "http://km-kminternal:8081" and svcports("km-kminternal") == {8081} and env("Deployment", "km-kmmcp")["MCP_PATH"] == "/km-knowledgemanagement/mcp" and env("Deployment", "km-kmmcp")["KNOWLEDGE_API_URL"] == "http://km-kmapi:8080/km-knowledgemanagement/tmf-api/knowledgeManagement/v5"'
expect "Keycloak JWKS and issuers" \
  'env("StatefulSet", "km-kmapi")["KEYCLOAK_JWKS_URL"] == "http://canvas-keycloak-headless.canvas.svc.cluster.local:8083/auth/realms/odari/protocol/openid-connect/certs" and env("StatefulSet", "km-kmapi")["KEYCLOAK_ISSUERS"] == "http://canvas-keycloak-headless.canvas.svc.cluster.local:8083/auth/realms/odari,https://kc.example.org/auth/realms/odari"' \
  --set keycloak.extraIssuers[0]=https://kc.example.org/auth/realms/odari
expect "the internal token Secret is mounted by the API and the workers" \
  'one("Secret", "km-km-internal-token")["data"]["token"] and all(any(v.get("secret", {}).get("secretName") == "km-km-internal-token" for v in pod(k, n)["volumes"]) for k, n in (("StatefulSet", "km-kmapi"), ("Deployment", "km-kmworker")))'
expect "non-root, read-only root filesystem, no capabilities" \
  'all(c["securityContext"]["readOnlyRootFilesystem"] and c["securityContext"]["runAsNonRoot"] and c["securityContext"]["capabilities"]["drop"] == ["ALL"] for d in kind("StatefulSet") + kind("Deployment") for c in d["spec"]["template"]["spec"]["containers"])'
expect "workloads carry the component labels" \
  'all(d["spec"]["template"]["metadata"]["labels"]["oda.tmforum.org/componentName"] == "km-knowledgemanagement" for d in kind("StatefulSet") + kind("Deployment"))'
expect "image tag <version>-<prereleaseSuffix>, or an override" \
  'pod("StatefulSet", "km-kmapi")["containers"][0]["image"] == "tmforumodacanvas/tmfc060-knowledge-api:0.1.0-ai-knowledge" and pod("Deployment", "km-kmmcp")["containers"][0]["image"] == "tmforumodacanvas/tmfc060-mcp-server:dev"' \
  --set mcpServer.tag=dev
expect "external database URL from a Secret" \
  'env("StatefulSet", "km-kmapi")["DATABASE_URL"] == {"secretKeyRef": {"name": "pg", "key": "url"}} and not [d for d in docs if d["metadata"]["name"].startswith("km-kmpg")]' \
  --set postgresql.enabled=false --set database.external.existingSecret=pg
expect "external database URL" \
  'env("StatefulSet", "km-kmapi")["DATABASE_URL"] == "postgresql://u:p@pg:5432/km"' \
  --set postgresql.enabled=false --set database.external.url=postgresql://u:p@pg:5432/km
expect "OpenAI-compatible key mounted as a file" \
  'env("StatefulSet", "km-kmapi")["OPENAI_API_KEY_FILE"] == "/etc/tmfc060/openai/api-key" and any(v.get("secret", {}).get("secretName") == "llm-key" for v in pod("StatefulSet", "km-kmapi")["volumes"])' \
  --set models.openaiCompatible.baseUrl=http://llm:8000/v1 --set models.openaiCompatible.apiKeySecret=llm-key
expect "no NetworkPolicy by default" 'kind("NetworkPolicy") == []'
expect "NetworkPolicy: internal port from the workers only" \
  'kind("NetworkPolicy")[0]["spec"]["ingress"][0]["ports"] == [{"port": 8081, "protocol": "TCP"}] and kind("NetworkPolicy")[0]["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "worker"' \
  --set networkPolicy.enabled=true
refuse "the API has no replicas value" "Additional property replicas" --set knowledgeApi.replicas=2
NO_STORES=1 refuse "no Qdrant" "set qdrant.external.url"

# PostgreSQL (#53)
expect "in-chart PostgreSQL by default, official image, one replica" \
  'one("StatefulSet", "km-kmpg")["spec"]["replicas"] == 1 and pod("StatefulSet", "km-kmpg")["containers"][0]["image"] == "postgres:17.12-alpine" and svcports("km-kmpg") == {5432} and one("Service", "km-kmpg")["spec"]["ports"][0]["name"].startswith("tcp-")'
expect "API DATABASE_URL built from the credentials Secret" \
  'env("StatefulSet", "km-kmapi")["PG_PASSWORD"] == {"secretKeyRef": {"name": "km-kmpg-credentials", "key": "password"}} and env("StatefulSet", "km-kmapi")["DATABASE_URL"] == "postgresql://tmfc060:$(PG_PASSWORD)@km-kmpg:5432/tmfc060"'
expect "credentials Secret: keys, kept on uninstall, not owned by the Component" \
  'sorted(one("Secret", "km-kmpg-credentials")["data"]) == ["database", "password", "url", "username"] and one("Secret", "km-kmpg-credentials")["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep" and "oda.tmforum.org/componentName" not in one("Secret", "km-kmpg-credentials")["metadata"]["labels"]'
expect "data PVC template without the Component name label" \
  '"oda.tmforum.org/componentName" not in one("StatefulSet", "km-kmpg")["spec"]["volumeClaimTemplates"][0]["metadata"]["labels"] and "oda.tmforum.org/componentName" not in one("StatefulSet", "km-kmapi")["spec"]["volumeClaimTemplates"][0]["metadata"].get("labels", {})'
expect "PostgreSQL non-root with a read-only root filesystem, probes by pg_isready" \
  'pod("StatefulSet", "km-kmpg")["securityContext"]["runAsUser"] == 70 and pod("StatefulSet", "km-kmpg")["containers"][0]["securityContext"]["readOnlyRootFilesystem"] and pod("StatefulSet", "km-kmpg")["containers"][0]["readinessProbe"]["exec"]["command"][0] == "pg_isready"'
expect "postgresql.existingSecret: no generated Secret" \
  'not kind("Secret") or "km-kmpg-credentials" not in [d["metadata"]["name"] for d in kind("Secret")] and env("StatefulSet", "km-kmapi")["PG_PASSWORD"]["secretKeyRef"]["name"] == "mypg" and env("StatefulSet", "km-kmpg")["POSTGRES_PASSWORD"]["secretKeyRef"]["name"] == "mypg"' \
  --set postgresql.existingSecret=mypg
refuse "in-chart and external database together" "not both" --set database.external.url=postgresql://u:p@pg:5432/km
refuse "no database at all" "with postgresql.enabled=false" --set postgresql.enabled=false

if [ "$failures" -gt 0 ]; then echo "$failures check(s) failed"; exit 1; fi
echo "all chart checks passed"
