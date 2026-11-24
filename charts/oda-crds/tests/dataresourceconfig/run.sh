#!/usr/bin/env bash
# DataResourceConfig CRD checks against a live API server (the CEL rules need one): applies the CRD rendered from
# charts/oda-crds, then server-side dry-runs every sample in samples/. Each sample's first line says what to expect:
#   # expect: accept
#   # expect: reject: <text the API server's error must contain>
# Nothing is created except the CRD itself. Run in CI on a throwaway kind cluster, or on a kind Canvas.
# Environment: NAMESPACE (default "default"), APPLY_CRD=0 to use the CRD already installed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CHART="$(cd "$HERE/../.." && pwd)"
NAMESPACE="${NAMESPACE:-default}"
CRD=dataresourceconfigs.oda.tmforum.org

if [ "${APPLY_CRD:-1}" = 1 ]; then
  # oda-crds takes global.certificate.appName from canvas-oda; only the DataResourceConfig CRD is rendered here
  helm template oda-crds "$CHART" --set global.certificate.appName=compcrdwebhook \
    --show-only templates/oda-dataresourceconfig-crd.yaml | kubectl apply -f - >/dev/null
fi
kubectl wait --for=condition=Established "crd/$CRD" --timeout=60s >/dev/null

failed=()
ok() { echo "ok   $1"; }
fail() { echo "FAIL $1"; failed+=("$1"); }

for f in "$HERE"/samples/*.yaml; do
  name="$(basename "$f" .yaml)"
  expect="$(sed -n '1s/^# expect: //p' "$f")"
  if out="$(kubectl apply --dry-run=server -n "$NAMESPACE" -f "$f" 2>&1)"; then
    if [ "$expect" = accept ]; then ok "$name accepted"; else fail "$name accepted, expected $expect"; fi
  else
    want="${expect#reject: }"
    if [ "$expect" = accept ]; then
      fail "$name rejected: $out"
    elif [[ "$out" == *"$want"* ]]; then
      ok "$name rejected ($want)"
    else
      fail "$name rejected without \"$want\": $out"
    fi
  fi
done

# Defaults: role consumer, access read, required true
got="$(kubectl apply --dry-run=server -n "$NAMESPACE" -f "$HERE/samples/valid-03-adr010-consumer.yaml" -o \
  jsonpath='{.spec.role} {.spec.dependentDataResources[0].access} {.spec.dependentDataResources[0].required}')"
if [ "$got" = "consumer read true" ]; then ok "defaults role, access, required: $got"; else fail "defaults: $got"; fi

# Printer columns
cols="$(kubectl get crd "$CRD" -o jsonpath='{.spec.versions[0].additionalPrinterColumns[*].name}')"
if [ "$cols" = "Provider Role Phase Resources Age" ]; then ok "printer columns: $cols"; else fail "printer columns: $cols"; fi

if [ ${#failed[@]} -gt 0 ]; then
  echo "${#failed[@]} check(s) failed"
  exit 1
fi
echo "all checks passed"
