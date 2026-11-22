# Spike S5: TMFOP006 delivery of subscribed events

Issue: [hw-irc-sni/oda-canvas#21](https://github.com/hw-irc-sni/oda-canvas/issues/21). Checked on 2026-11-22 against
`ai-knowledge` (8ddea008 lineage), `origin/main`, `ai-gateways`, `ai-registries`, `ai-hvs` and `upstream/main`,
and on the local kind Canvas (canvas-oda 1.2.6-rc8).

## Question

Is the Event Management operator (TMFOP006) enough to deliver the TMF639, TMF638, TMF642, TMF628 and TMF921
events that TMFC060 declares in `eventNotification.subscribedEvents`?

## Answer: no

| Finding | Evidence |
|---|---|
| TMFOP006 has no implementation | [`TMFOP006-Event-Management/README.md`](../../../../operators/TMFOP006-Event-Management/README.md) says *Implementation Planned*; the folder holds only the README. `source/operators/AGENTS.md` lists it as "README only, Planned" |
| The Component operator turns each subscribed event into a `SubscribedNotification` custom resource | [`componentOperator.py:791`](../../../../operators/TMFOP001-Component-Management/component-management/componentOperator.py#L791) (`subscribedEvents` handler) and [`:2446`](../../../../operators/TMFOP001-Component-Management/component-management/componentOperator.py#L2446) (`createSubscribedNotificationResource`) |
| Nothing watches `SubscribedNotification` | Searching all six branches for `subscribednotification` finds only the Component operator, operator RBAC rules and sequence diagrams |
| On the kind Canvas the CRD exists, but nothing acts on it | `subscribednotifications.oda.tmforum.org` is installed; `kubectl get subscribednotifications -A` returns no resources, and no operator handles them |

So declaring `subscribedEvents` alone delivers nothing.

## How a dependent API is resolved (TMFOP005, simple operator)

- A `DependentAPI` is matched to an `ExposedAPI` by **exact equality of the specification URL**, preferring other
  components' APIs to the component's own
  ([`customresource_service_discovery`, `dependentApiSimpleOperator.py:180`](../../../../operators/TMFOP005-Dependency-Management/simple-dependency-management/docker/src/dependentApiSimpleOperator.py#L180)).
  With `COMPONENT_REGISTRY_URL` set, the Component Registry is searched instead, also by specification.
- The resolved URL is the provider ExposedAPI's `status.apiStatus.url` (its gateway URL). It is written to the
  DependentAPI's `status.depapiStatus.url`, which also gets `status.implementation.ready: true`
  ([`setDependentAPIStatus`, `:326`](../../../../operators/TMFOP005-Dependency-Management/simple-dependency-management/docker/src/dependentApiSimpleOperator.py#L326)).
- It is also registered in the **canvas-info service** (TMF638 Service Inventory), keyed by `componentName` and
  `dependencyName`, with state `active`
  ([`updateServiceInventory`, `:295`](../../../../operators/TMFOP005-Dependency-Management/simple-dependency-management/docker/src/dependentApiSimpleOperator.py#L295)).
  Reference components query this service at runtime (`CANVAS_INFO_HOST_PORT`).

## Decision (chosen by the user, for #46)

Register directly on each provider's event hub, with the URL found through canvas-info:

1. The TMFC060 Component CR declares TMF639, TMF638, TMF642, TMF628 and TMF921 as **optional `dependentAPIs`**,
   with **the same specification URLs the providers expose**, because TMFOP005 matches on exact URL.
2. At runtime TMFC060 reads each resolved URL from the canvas-info service, the way the reference components do.
3. For every knowledge-base whose `source[]` includes that API, it `POST`s `/hub` on the provider with a callback to
   its own `/listener/...` endpoint. It re-registers when the URL changes, and deletes the hub when no
   knowledge-base needs the source any more.
4. The CR keeps its `subscribedEvents`, so that a future TMFOP006 can take over the delivery.

Open points for #46:
- **Reachability:** the provider must be able to reach the callback. Its URL is TMFC060's own ExposedAPI URL, so the
  question is the same as spike S2.
- **Authentication:** how incoming notifications are authenticated.
- **The kind Canvas:** canvas-info is not deployed on the local kind Canvas, so testing needs it enabled.
