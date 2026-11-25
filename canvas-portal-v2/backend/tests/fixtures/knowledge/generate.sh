#!/usr/bin/env bash
# Regenerates ri_resources.json, the Resource Inventory payload used by tests/test_knowledge_instances.py.
# It renders the TMFC060 chart's Component CR (release kmk in namespace components), derives the ExposedAPIs the
# component operator creates from it, adds a non-TMFC060 component, and runs them through TMF639 Resource
# Inventory's own projection code (convertComponentToResource, convertExposedAPIToResource).
# Needs: helm, python3 with PyYAML, node; run `npm install` in the Resource Inventory folder first.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../../../.." && pwd)
RI="$ROOT/source/tmf-services/TMF639_Resource_Inventory"
helm template kmk "$ROOT/source/components/TMFC060-KnowledgeManagement/chart" -n components \
  | python3 -c '
import json, sys, yaml
component = next(d for d in yaml.safe_load_all(sys.stdin) if d and d.get("kind") == "Component")
print(json.dumps(component, default=str))' \
  | (cd "$RI" && OUT="$HERE/ri_resources.json" node -e '
const KubernetesResourceService = require("./services/KubernetesResourceService");
const service = new KubernetesResourceService();
const component = JSON.parse(require("fs").readFileSync(0, "utf8"));
const name = component.metadata.name, namespace = "components";
component.metadata = { ...component.metadata, namespace, resourceVersion: "11", creationTimestamp: "2026-11-24T10:00:00Z" };
component.status = { "summary/status": { deployment_status: "Complete" } };
const exposedAPIs = [...component.spec.coreFunction.exposedAPIs, ...component.spec.managementFunction.exposedAPIs]
  .map(api => ({
    metadata: { name: `${name}-${api.name}`, namespace, resourceVersion: "12", creationTimestamp: "2026-11-24T10:01:00Z",
                labels: { "oda.tmforum.org/componentName": name } },
    spec: api,
    status: { apiStatus: { url: `https://127.0.0.1${api.path}`, developerUI: api.developerUI ? `https://127.0.0.1${api.developerUI}` : undefined },
              implementation: { ready: true } }
  }));
const other = {
  metadata: { name: "pc-productcatalogmanagement", namespace, resourceVersion: "5", creationTimestamp: "2026-11-15T09:43:00Z",
              labels: {} },
  spec: { componentMetadata: { id: "TMFC001", name: "productcatalogmanagement", version: "1.4.5" },
          coreFunction: { exposedAPIs: [] } },
  status: { "summary/status": { deployment_status: "Complete" } }
};
const resources = [
  service.convertComponentToResource(component, exposedAPIs),
  ...exposedAPIs.map(api => service.convertExposedAPIToResource(api, component)),
  service.convertComponentToResource(other, [])
];
require("fs").writeFileSync(process.env.OUT, JSON.stringify(resources, null, 2) + "\n");
' >/dev/null)
echo "wrote $HERE/ri_resources.json"
