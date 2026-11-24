#!/usr/bin/env bash
# Offline checks of the TMFC060 chart: helm lint, renders that must succeed (with what they must contain) and
# values that must be refused. Needs helm and python3 with PyYAML. Run from anywhere: chart/tests/check.sh
set -euo pipefail
CHART="$(cd "$(dirname "$0")/.." && pwd)"
failures=0
pass() { echo "ok   $1"; }
fail() { echo "FAIL $1"; failures=$((failures + 1)); }

# The Qdrant subchart (Chart.lock) must be in charts/
if ! ls "$CHART"/charts/qdrant-*.tgz >/dev/null 2>&1; then
  helm repo add qdrant https://qdrant.github.io/qdrant-helm >/dev/null 2>&1 || true
  helm dependency build "$CHART" >/dev/null
fi

render() { helm template km "$CHART" -n components "$@"; }

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

if helm lint "$CHART" >/dev/null; then pass "helm lint"; else fail "helm lint"; helm lint "$CHART" || true; fi

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
  'all(c["securityContext"]["readOnlyRootFilesystem"] and c["securityContext"]["runAsNonRoot"] and c["securityContext"]["capabilities"]["drop"] == ["ALL"] for d in kind("StatefulSet") + kind("Deployment") for c in d["spec"]["template"]["spec"]["containers"] + (d["spec"]["template"]["spec"].get("initContainers") or []))'
# The Qdrant subchart's labels can't be templated from values: it carries the componentId label only
expect "workloads carry the component labels" \
  'all(d["spec"]["template"]["metadata"]["labels"]["oda.tmforum.org/componentName"] == "km-knowledgemanagement" for d in kind("StatefulSet") + kind("Deployment") if d["metadata"]["name"] != "km-qdrant") and pod("StatefulSet", "km-qdrant") and one("StatefulSet", "km-qdrant")["spec"]["template"]["metadata"]["labels"]["oda.tmforum.org/componentId"] == "TMFC060"'
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

# Qdrant (#54)
expect "Qdrant subchart by default, v1.20.2, API key on" \
  'pod("StatefulSet", "km-qdrant")["containers"][0]["image"] == "docker.io/qdrant/qdrant:v1.20.2-unprivileged" and "api-key" in one("Secret", "km-qdrant-apikey")["data"]'
expect "API reaches the subchart Qdrant with its key file" \
  'env("StatefulSet", "km-kmapi")["QDRANT_URL"] == "http://km-qdrant:6333" and env("StatefulSet", "km-kmapi")["QDRANT_API_KEY_FILE"] == "/etc/tmfc060/qdrant/api-key" and any(v.get("secret", {}).get("secretName") == "km-qdrant-apikey" and v["secret"]["items"] == [{"key": "api-key", "path": "api-key"}] for v in pod("StatefulSet", "km-kmapi")["volumes"])'
expect "Qdrant PVC template without the Component name label" \
  '"oda.tmforum.org/componentName" not in one("StatefulSet", "km-qdrant")["spec"]["volumeClaimTemplates"][0]["metadata"].get("labels", {})'
expect "external Qdrant with its key Secret" \
  'not [d for d in docs if d["metadata"]["name"].startswith("km-qdrant") and "helm.sh/hook" not in d["metadata"].get("annotations", {})] and env("StatefulSet", "km-kmapi")["QDRANT_URL"] == "https://qdrant.example.org" and any(v.get("secret", {}).get("secretName") == "qkey" for v in pod("StatefulSet", "km-kmapi")["volumes"])' \
  --set qdrant.enabled=false --set externalQdrant.url=https://qdrant.example.org --set externalQdrant.apiKeySecret=qkey
expect "external Qdrant without a key" \
  '"QDRANT_API_KEY_FILE" not in env("StatefulSet", "km-kmapi")' \
  --set qdrant.enabled=false --set externalQdrant.url=http://qdrant:6333
refuse "subchart and external Qdrant together" "not both" --set externalQdrant.url=http://qdrant:6333
refuse "no Qdrant at all" "set externalQdrant.url" --set qdrant.enabled=false
refuse "a fixed Qdrant API key string" "qdrant.apiKey" --set qdrant.apiKey=secret

# ModelGatewayService RBAC (#55)
RBAC_KINDS='[d for d in docs if d["kind"] in ("Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding")]'
MGS_ON=(--set modelResolver.modelGatewayService.enabled=true --set modelResolver.modelGatewayService.name=llm
  --set modelResolver.modelGatewayService.chatModel=chat --set modelResolver.modelGatewayService.embeddingModel=embed)
expect "no RBAC, no MGS env and no API token by default" \
  "not $RBAC_KINDS"' and "MODEL_GATEWAY_SERVICE" not in env("StatefulSet", "km-kmapi") and one("ServiceAccount", "km-kmapi")["automountServiceAccountToken"] is False and one("ServiceAccount", "km-km")["automountServiceAccountToken"] is False and pod("StatefulSet", "km-kmapi")["serviceAccountName"] == "km-kmapi" and all(pod(k, n)["serviceAccountName"] == "km-km" for k, n in (("Deployment", "km-kmworker"), ("Deployment", "km-kmmcp"), ("StatefulSet", "km-kmpg")))'
expect "MGS on: CRD read limited to its name, Role verbs, bindings to the API ServiceAccount, env" \
  'one("ClusterRole", "components-km-kmapi-mgs-crd")["rules"] == [{"apiGroups": ["apiextensions.k8s.io"], "resources": ["customresourcedefinitions"], "resourceNames": ["modelgatewayservices.oda.tmforum.org"], "verbs": ["get"]}] and one("Role", "km-kmapi-mgs")["rules"] == [{"apiGroups": ["oda.tmforum.org"], "resources": ["modelgatewayservices"], "verbs": ["get", "list"]}, {"apiGroups": [""], "resources": ["services"], "verbs": ["get"]}] and all(b["subjects"] == [{"kind": "ServiceAccount", "name": "km-kmapi", "namespace": "components"}] for b in kind("RoleBinding") + kind("ClusterRoleBinding")) and one("Role", "km-kmapi-mgs")["metadata"]["namespace"] == "components" and env("StatefulSet", "km-kmapi")["MODEL_GATEWAY_SERVICE"] == "llm" and env("StatefulSet", "km-kmapi")["MGS_EMBEDDING_MODEL"] == "embed" and one("ServiceAccount", "km-kmapi")["automountServiceAccountToken"] is True' \
  "${MGS_ON[@]}"
expect "MGS in another namespace: the Role and its binding are there" \
  'one("Role", "km-kmapi-mgs")["metadata"]["namespace"] == "ai" and one("RoleBinding", "km-kmapi-mgs")["metadata"]["namespace"] == "ai" and env("StatefulSet", "km-kmapi")["MODEL_GATEWAY_SERVICE"] == "ai/llm"' \
  "${MGS_ON[@]}" --set modelResolver.modelGatewayService.name=ai/llm
refuse "MGS on without a name" "modelResolver.modelGatewayService.name" \
  --set modelResolver.modelGatewayService.enabled=true --set modelResolver.modelGatewayService.chatModel=c --set modelResolver.modelGatewayService.embeddingModel=e
refuse "MGS on without models" "modelResolver.modelGatewayService.chatModel" \
  --set modelResolver.modelGatewayService.enabled=true --set modelResolver.modelGatewayService.name=llm

# Provider DataResourceConfig (#56)
DRC_SPEC='{"provider": "odaComponent", "role": "provider", "providedDataResources": [{"name": "knowledge-vectors", "type": "vectorStore", "description": "Hybrid dense and sparse vector store, one collection per knowledge-base", "engine": "qdrant", "exposedAPI": "knowledgemanagement", "mcpAPI": "knowledgemcp", "retrieval": {"hybrid": True, "scoreThreshold": 0.3, "topK": 8}}, {"name": "knowledge-graph", "type": "knowledgeGraph", "description": "RDF graph store, one named graph per knowledge-base and per ontology module", "engine": "pyoxigraph", "exposedAPI": "knowledgemanagement", "mcpAPI": "knowledgemcp"}]}'
expect "no DataResourceConfig without the CRD" 'kind("DataResourceConfig") == []'
expect "DataResourceConfig when the Canvas has the CRD (no flag), as in ADR-010" \
  'kind("DataResourceConfig")[0]["spec"] == '"$DRC_SPEC"' and kind("DataResourceConfig")[0]["metadata"]["name"] == "km-knowledgemanagement" and kind("DataResourceConfig")[0]["metadata"]["labels"]["oda.tmforum.org/componentId"] == "TMFC060" and kind("DataResourceConfig")[0]["metadata"]["labels"]["oda.tmforum.org/componentName"] == "km-knowledgemanagement"' \
  --api-versions oda.tmforum.org/v1alpha1/DataResourceConfig
expect "DataResourceConfig forced by the flag" 'len(kind("DataResourceConfig")) == 1' --set dataResourceConfig.enabled=true
expect "the APIs it names are the Component's" \
  '{r["exposedAPI"] for r in kind("DataResourceConfig")[0]["spec"]["providedDataResources"]} <= {a["name"] for a in comp["coreFunction"]["exposedAPIs"]} and {r["mcpAPI"] for r in kind("DataResourceConfig")[0]["spec"]["providedDataResources"]} <= {a["name"] for a in comp["coreFunction"]["exposedAPIs"] if a["apiType"] == "mcp"}' \
  --set dataResourceConfig.enabled=true
refuse "unknown data resource type" "providedDataResources.0.type" --set dataResourceConfig.providedDataResources[0].type=blob
refuse "an exposedAPI the Component lacks" "providedDataResources.0.exposedAPI" --set dataResourceConfig.providedDataResources[0].exposedAPI=other
refuse "topK above the API limit" "topK" --set dataResourceConfig.providedDataResources[0].retrieval.topK=500
refuse "no provided data resources" "providedDataResources" --set-json 'dataResourceConfig.providedDataResources=[]'

# Ontology sources (#57)
mounts='{m["mountPath"]: m["name"] for m in pod("StatefulSet", "km-kmapi")["containers"][0]["volumeMounts"]}'
expect "no ontology sources by default" \
  '"ONTOLOGY_SOURCES" not in env("StatefulSet", "km-kmapi") and not [d for d in kind("ConfigMap") if d["metadata"]["name"] == "km-km-ontology-sources"] and env("StatefulSet", "km-kmapi")["ONTOLOGY_SYNC_INTERVAL"] == "900"'
expect "the fto demo: a git source with its credential Secret" \
  '__import__("json").loads(one("ConfigMap", "km-km-ontology-sources")["data"]["sources.json"]) == [{"name": "fto", "git": {"url": "https://github.com/hw-irc-sni/tfo.git", "ref": "main", "secretRef": "tfo-git"}, "shapes": [{"id": "tio-shacl-ext", "path": "implementation/tio-shacl-ext/rdf"}]}] and env("StatefulSet", "km-kmapi")["ONTOLOGY_SOURCES"] == {"configMapKeyRef": {"name": "km-km-ontology-sources", "key": "sources.json"}} and '"$mounts"'.get("/etc/tmfc060/git/tfo-git") == "git-fto"' \
  -f "$CHART/values-fto-demo.yaml"
expect "a secretMount source is mounted under the mount root" \
  '__import__("json").loads(one("ConfigMap", "km-km-ontology-sources")["data"]["sources.json"]) == [{"name": "local", "mount": {"path": "/etc/tmfc060/ontologies/local/repo"}}] and '"$mounts"'.get("/etc/tmfc060/ontologies/local") == "ontology-local"' \
  --set-json 'ontologySources=[{"name":"local","kind":"secretMount","secretName":"onto","path":"repo"}]'
expect "the API restarts when the sources change" \
  'one("StatefulSet", "km-kmapi")["spec"]["template"]["metadata"]["annotations"]["checksum/ontology-sources"] != "'"$(render | python3 -c 'import sys,yaml; print([d for d in yaml.safe_load_all(sys.stdin) if d and d["kind"]=="StatefulSet" and d["metadata"]["name"]=="km-kmapi"][0]["spec"]["template"]["metadata"]["annotations"]["checksum/ontology-sources"])')"'"' \
  -f "$CHART/values-fto-demo.yaml"
expect "secretMount items lay the flat Secret keys out in folders" \
  'next(v for v in pod("StatefulSet", "km-kmapi")["volumes"] if v["name"] == "ontology-local")["secret"]["items"] == [{"key": "modules.json", "path": "modules.json"}, {"key": "core.ttl", "path": "foundational/core.ttl"}]' \
  --set-json 'ontologySources=[{"name":"local","kind":"secretMount","secretName":"onto","items":[{"key":"modules.json","path":"modules.json"},{"key":"core.ttl","path":"foundational/core.ttl"}]}]'
refuse "unknown source kind" "ontologySources.0" --set-json 'ontologySources=[{"name":"x","kind":"svn","url":"https://x"}]'
refuse "git source without a URL" "ontologySources.0" --set-json 'ontologySources=[{"name":"x","kind":"git"}]'
refuse "git source over http" "ontologySources.0" --set-json 'ontologySources=[{"name":"x","kind":"git","url":"http://example.org/r.git"}]'
refuse "secretMount without a Secret" "ontologySources.0" --set-json 'ontologySources=[{"name":"x","kind":"secretMount"}]'
refuse "duplicate source names" "used twice" --set-json 'ontologySources=[{"name":"x","kind":"git","url":"https://a"},{"name":"x","kind":"git","url":"https://b"}]'
refuse "sync more often than every minute" "intervalSeconds" --set ontologySync.intervalSeconds=5

if [ "$failures" -gt 0 ]; then echo "$failures check(s) failed"; exit 1; fi
echo "all chart checks passed"
