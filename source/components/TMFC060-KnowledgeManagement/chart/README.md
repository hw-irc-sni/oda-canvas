# TMFC060 Knowledge Management chart

The Helm chart of the TMFC060 Knowledge Management ODA Component. It renders a `Component` (`oda.tmforum.org/v1`) that the Canvas operators turn into ExposedAPIs, Keycloak roles and DependentAPIs.

The Component is named `<release>-<component.name>` (by default `<release>-knowledgemanagement`). That name prefixes every API path, because the Canvas gateway routes by prefix without rewriting. It is also the audience the API requires in tokens.

## What the Component declares

| Part | Content |
|---|---|
| `coreFunction.exposedAPIs` | `knowledgemanagement` (openapi, TMFxxx, `/<component>/tmf-api/knowledgeManagement/v5`), `processflow` (openapi, TMF701 v5 subset, `/<component>/tmf-api/process/v5`), `knowledgemcp` (mcp, `/<component>/mcp`) |
| `coreFunction.dependentAPIs` | Optional dependencies, each rendered only when `dependentAPIs.<name>.enabled` is true (see below) |
| `eventNotification.subscribedEvents` | The events of the enabled ingestion sources, for a future Event Management operator. Today the API registers on each provider's hub itself |
| `managementFunction.exposedAPIs` | `metrics` (prometheus, port 4000, `/<component>/metrics`) |
| `securityFunction` | `canvasSystemRole` and the static roles `knowledge-viewer`, `knowledge-editor`, `ontology-editor`, `ontology-reviewer`, `ontology-approver`. There is no TMF669 partyrole API |

The specification URLs of `knowledgemanagement` and `processflow` default to the OAS files the API serves at its in-cluster Service (`<base>/openapi.yaml`, with Swagger UI at `<base>/docs`). Set `component.knowledgeApiSpecification` and `component.processFlowApiSpecification` to publish other URLs.

## Workloads

| Object | Kind | Role |
|---|---|---|
| `<release>-kmapi` | StatefulSet, **always 1 replica** | The knowledge API. Its pyoxigraph (RocksDB) store has a single writer and lives on the `store` PVC (`knowledgeApi.store.size`, `storageClass`). Ports: 8080 (API), 8081 (internal API of the workers), 4000 (metrics) |
| `<release>-kmworker` | Deployment (`pipelineWorker.replicas`, `concurrency`) | Pipeline workers. They claim tasks through the internal API, with the token of the `<release>-km-internal-token` Secret, which is generated once and kept across upgrades |
| `<release>-kmmcp` | Deployment (`mcpServer.replicas`) | MCP server at `/<component>/mcp`. It forwards the caller's token to the API |
| `<release>-kmapi`, `<release>-kminternal`, `<release>-kmapi-hl`, `<release>-kmmcp` | Services | Public API and metrics (the ExposedAPIs' targets), the internal API, the StatefulSet's governing Service, and MCP |
| `<release>-kmapi`, `<release>-km` | ServiceAccounts | The API's own, which gets a token only with `modelResolver.modelGatewayService.enabled`; and one for the worker, MCP and PostgreSQL, which never gets a token |
| `<release>-kmapi-internal` | NetworkPolicy, `networkPolicy.enabled` (off by default) | Only the workers may reach the internal port; it needs a CNI that enforces policies |

Every container runs as uid 10001 with a read-only root filesystem, no privilege escalation and no capabilities. `/tmp` is an emptyDir.

The image tag defaults to `<version>-<prereleaseSuffix>` (`0.1.0-ai-knowledge`), the tag the build workflows push. Set `<workload>.tag` to override it. The images aren't on Docker Hub yet, so on kind build and load them:

```bash
for p in "knowledgeApiMicroservice tmfc060-knowledge-api" "pipelineWorkerMicroservice tmfc060-pipeline-worker" "mcpServerMicroservice tmfc060-mcp-server"; do
  set -- $p; docker build -t tmforumodacanvas/$2:0.1.0-ai-knowledge ../$1
  kind load docker-image tmforumodacanvas/$2:0.1.0-ai-knowledge --name <cluster>
done
```

### Stores and settings

| Values | Meaning |
|---|---|
| `postgresql.*` | The in-chart PostgreSQL (default): `<release>-kmpg`, a StatefulSet on `postgres:17.12-alpine` with a `data` PVC (`persistence.size` 8Gi, `storageClass`). Credentials are generated into `<release>-kmpg-credentials` (`username`, `password`, `database`, `url`), or the password comes from `existingSecret` (key `password`, URL-safe) |
| `database.external.url` or `.existingSecret`/`.key` | An external PostgreSQL instead, with `postgresql.enabled=false`. Setting both is refused |
| `qdrant.*` | The Qdrant subchart (default): the official chart 1.20.2, Qdrant v1.20.2 (unprivileged image, uid 1000), `<release>-qdrant` with a 10Gi PVC. Everything under `qdrant:` except `enabled` goes to that chart. `apiKey: true` generates the key into `<release>-qdrant-apikey`, and the API reads it as a file (`QDRANT_API_KEY_FILE`) |
| `externalQdrant.url`, `.apiKeySecret`/`.apiKeySecretKey` | An external Qdrant instead, with `qdrant.enabled=false`, and its optional API key Secret. Setting both is refused |
| `keycloak.baseUrl`, `realm`, `extraIssuers` | Token validation: keys from the in-cluster JWKS. A token's issuer must be the in-cluster realm URL or one of `extraIssuers`, such as the public URL the portal uses. The audience must contain the Component name |
| `modelResolver.modelGatewayService.*` (`enabled`, `name` as `name` or `namespace/name`, `chatModel`, `embeddingModel`) | Model route 1: a ModelGatewayService custom resource, read through the Kubernetes API. **Opt-in** (see below) |
| `models.agentgateway.*`, `models.openaiCompatible.*` (API key from a Secret, mounted as a file), `models.embeddingDimension` | Model routes 2 and 3. The workers call models through the API |
| `events.callbackBase`, `callbackAllowedHosts`, `canvasInfoUrl`, `hrefBase` | Event delivery and event-driven ingestion. With no `callbackBase`, nothing registers on TMF providers |

### ModelGatewayService access (opt-in)

ODA components don't normally call the Kubernetes API. With `modelResolver.modelGatewayService.enabled=true`, and only then, the chart:

- mounts a token for the API's ServiceAccount `<release>-kmapi`
- creates a ClusterRole and ClusterRoleBinding `<namespace>-<release>-kmapi-mgs-crd`: `get` on the single CRD `modelgatewayservices.oda.tmforum.org`, the existence check
- creates a Role and RoleBinding `<release>-kmapi-mgs` in the ModelGatewayService's namespace: `get` and `list` on `modelgatewayservices`, and `get` on `services`, for the endpoint when the resource names none
- sets `MODEL_GATEWAY_SERVICE`, `MGS_CHAT_MODEL` and `MGS_EMBEDDING_MODEL` on the API

With the flag off (the default), none of this is rendered, and no pod of the component has a Kubernetes API token.

### Removing the data

`helm uninstall` keeps the data:

- the PostgreSQL `data-<release>-kmpg-0` PVC
- the API's `store-<release>-kmapi-0` PVC (the Kubernetes default for StatefulSet PVCs)
- the Qdrant `qdrant-storage-<release>-qdrant-0` PVC. Its API key Secret goes with the release, which is harmless: the key guards access, not the data, and a reinstall generates a new one that the API reads
- the credentials Secret `<release>-kmpg-credentials` (`helm.sh/resource-policy: keep`), so the kept database can still be opened

None of them carries the `oda.tmforum.org/componentName` label, so the Component operator doesn't adopt them and delete them with the Component. A reinstall with the same release name finds the data and the migrations again. To wipe everything after an uninstall:

```bash
kubectl delete pvc -n components data-<release>-kmpg-0 store-<release>-kmapi-0 qdrant-storage-<release>-qdrant-0
kubectl delete secret -n components <release>-kmpg-credentials
```

### Qdrant and open files

Qdrant keeps files open for every collection segment, and the API creates one collection per knowledge-base. Below about 65535 open files (`nofile`) it fails with "Too many open files" once there are a handful of collections. Kubernetes has no per-pod setting for this: the limit comes from the container runtime. kind gives pods 1048576, and so do most containerd installs. Check with `kubectl exec <release>-qdrant-0 -- sh -c 'ulimit -n'` on other clusters.

The subchart needs `helm dependency build` (the repository is `https://qdrant.github.io/qdrant-helm`) before `helm install` from a checkout; `tests/check.sh` runs it when `charts/` is empty.

## Dependent APIs

The Component operator counts every DependentAPI towards the Component's deployment status, `required: false` included. A dependency without a provider would therefore keep a plain install from completing, so every dependency is off by default.

| Name | API | Default specification | Use |
|---|---|---|---|
| `tmf639` | TMF639 Resource Inventory | v5, Open API Table | ingestion source |
| `tmf638` | TMF638 Service Inventory | v5, Open API Table | ingestion source |
| `tmf642` | TMF642 Alarm Management | v5.0.1, Open API Table | ingestion source |
| `tmf628` | TMF628 Performance Management | v4, tmforum-apis GitHub (no v5 published) | ingestion source |
| `tmf921` | TMF921 Intent Management | v5, Open API Table | ingestion source |
| `aimanagement` | TMF915 AI Management | v4, Open API Table (no v5 published) | |
| `policymanagement` | TMF723 Policy Management | none (no public specification): set one to enable it | |
| `eventmanagement` | TMF688 Event Management | v4, tmforum-apis GitHub (no v5 published) | |
| `partymanagement` | TMF632 Party Management | v5, Open API Table | |
| `partyrolemanagement` | TMF669 Party Role Management | v5, Open API Table | |

The Dependency Management operator (TMFOP005) resolves a DependentAPI to the ExposedAPI with exactly the same specification URL, so set `dependentAPIs.<name>.specification` to the URL your provider exposes. Keep the names of the five ingestion sources. TMFOP005 records the name as `dependencyName` in canvas-info, and the API looks up a knowledge-base source of kind `tmf639` under the dependency `tmf639`, unless the source sets `dependentApiName`.

```bash
helm install km . -n components --set dependentAPIs.tmf639.enabled=true --set dependentAPIs.tmf921.enabled=true
```

## Checks

`tests/check.sh` runs `helm lint`, renders the chart with several value sets and checks the result. It also checks that invalid values are refused. CI runs it in the `chart` job of `tmfc060-unit-tests.yml`. To check the Component against the CRD of a running Canvas:

```bash
helm template km . -n components | kubectl apply --dry-run=server -n components -f -
```
