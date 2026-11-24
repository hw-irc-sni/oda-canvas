# TMFC060 Knowledge Management

The TMFC060 Knowledge Management ODA Component. It provides:

- **knowledge-bases:** vector, graph or hybrid stores of knowledge extracted from documents and TMF events
- **a governed, federated ontology:** modules, mappings, reviews and validation

It exposes three APIs:

- the TMFxxx Knowledge Management v5 API
- a TMF701 Process Management v5 subset, for ingestion processes
- an MCP server, for AI agents

The architecture is in [`Knowledge-Management-design.md`](../../../Knowledge-Management-design.md).

| Folder | Content |
|---|---|
| [`knowledgeApiMicroservice/`](knowledgeApiMicroservice/README.md) | The knowledge API: TMFxxx and TMF701, pyoxigraph store, PostgreSQL, Qdrant, model routes, events, governance, federation |
| [`pipelineWorkerMicroservice/`](pipelineWorkerMicroservice/README.md) | The ingestion workers: KM-FX-01 to KM-FX-06 |
| [`mcpServerMicroservice/`](mcpServerMicroservice/README.md) | The MCP server: ontology and knowledge-base tools |
| [`chart/`](chart/README.md) | The Helm chart: the Component, the workloads, PostgreSQL, Qdrant (subchart), optional RBAC for the ModelGatewayService route, the provider DataResourceConfig and ontology sources |
| [`dev/`](dev/docker-compose.yaml) | PostgreSQL and Qdrant for local development |
| [`spikes/`](spikes/) | The Phase 2 spikes (token roles, SPARQL SERVICE, event delivery) |

## Install on a kind Canvas

The steps below assume a kind Canvas installed per the repository docs (`canvas-oda`), whose gateway is on `https://127.0.0.1`. The [chart README](chart/README.md) describes every value.

1. **Images.** They aren't published on Docker Hub yet, so build them and load them into kind. The tag is the chart's default, `<version>-<prereleaseSuffix>`:

   ```bash
   for p in "knowledgeApiMicroservice tmfc060-knowledge-api" "pipelineWorkerMicroservice tmfc060-pipeline-worker" \
            "mcpServerMicroservice tmfc060-mcp-server"; do
     set -- $p
     docker build -t tmforumodacanvas/$2:0.1.0-ai-knowledge $1
     kind load docker-image tmforumodacanvas/$2:0.1.0-ai-knowledge --name <cluster>
   done
   ```

2. **The subchart.**

   ```bash
   helm repo add qdrant https://qdrant.github.io/qdrant-helm
   helm dependency build chart
   ```

3. **A model route.** The pipeline needs a chat model and an embedding model, both at one OpenAI-compatible base URL. For example, the Canvas agentgateway, once it routes an embedding model:

   ```bash
   MODELS="--set models.agentgateway.url=http://agentgateway.canvas.svc.cluster.local/v1 \
     --set models.agentgateway.chatModel=qwen3.5:9b --set models.agentgateway.embeddingModel=bge-m3"
   ```

   Alternatively use `models.openaiCompatible.*` (with an API key Secret), or a ModelGatewayService (`modelResolver.modelGatewayService.*`). Without any route the component installs, but ingestion can't extract or embed.

4. **Install** into `components`. The ontology sources are optional, for example `-f chart/values-fto-demo.yaml` with its credential Secret:

   ```bash
   helm install km chart -n components $MODELS
   kubectl get components -n components km-knowledgemanagement   # DEPLOYMENT_STATUS: Complete
   ```

   The Canvas then creates:
   - the ExposedAPIs `knowledgemanagement`, `processflow`, `knowledgemcp` and `metrics` under `https://127.0.0.1/km-knowledgemanagement/…`
   - the Keycloak client `km-knowledgemanagement`, with the roles `knowledge-viewer`, `knowledge-editor`, `ontology-editor`, `ontology-reviewer`, `ontology-approver` and `canvasRole`

   Users need those client roles. The portal's token (a password grant on `canvas-portal`) carries them.

5. **Check.** [`chart/tests/kind/run.sh`](chart/tests/kind/run.sh) installs its own release and runs the smoke checks and the PHASE 2 pipeline scenario through the gateway with a real token. It takes the same model arguments, and it cleans up after itself, including its temporary Keycloak user:

   ```bash
   chart/tests/kind/run.sh $MODELS
   ```

6. **Uninstall.** `helm uninstall km -n components`. The data PVCs and the PostgreSQL credentials are kept; the chart README says how to wipe them.

## Tests

- `uv run pytest -q` in each microservice runs the unit and integration tests (Docker needed for testcontainers).
- `chart/tests/check.sh` runs the offline chart checks.
- CI runs both (`.github/workflows/tmfc060-unit-tests.yml`). The kind run above is manual.
