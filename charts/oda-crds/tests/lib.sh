# Shared by the CRD sample checks. Each sample's first line says what to expect:
#   # expect: accept
#   # expect: reject: <text the API server's error must contain>
# Samples are only server-side dry-run: nothing is created except the CRD.
CHART="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-default}"
failed=()
ok() { echo "ok   $1"; }
fail() { echo "FAIL $1"; failed+=("$1"); }

# apply_crd <template file> <crd name>: render one CRD of the chart, apply it (unless APPLY_CRD=0), wait for it
apply_crd() {
  if [ "${APPLY_CRD:-1}" = 1 ]; then
    # oda-crds takes global.certificate.appName from canvas-oda
    helm template oda-crds "$CHART" --set global.certificate.appName=compcrdwebhook \
      --show-only "templates/$1" | kubectl apply -f - >/dev/null
  fi
  kubectl wait --for=condition=Established "crd/$2" --timeout=60s >/dev/null
}

# run_samples <samples dir>
run_samples() {
  local f name expect out want
  for f in "$1"/*.yaml; do
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
}

finish() {
  if [ ${#failed[@]} -gt 0 ]; then
    echo "${#failed[@]} check(s) failed"
    exit 1
  fi
  echo "all checks passed"
}
