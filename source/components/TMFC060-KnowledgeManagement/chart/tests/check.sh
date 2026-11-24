#!/usr/bin/env bash
# Offline checks of the TMFC060 chart: helm lint, renders that must succeed (with what they must contain) and
# values that must be refused. Needs helm and python3 with PyYAML. Run from anywhere: chart/tests/check.sh
set -euo pipefail
CHART="$(cd "$(dirname "$0")/.." && pwd)"
failures=0
pass() { echo "ok   $1"; }
fail() { echo "FAIL $1"; failures=$((failures + 1)); }

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

if [ "$failures" -gt 0 ]; then echo "$failures check(s) failed"; exit 1; fi
echo "all chart checks passed"
