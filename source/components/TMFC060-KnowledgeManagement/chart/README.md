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
