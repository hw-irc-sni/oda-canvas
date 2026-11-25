# TMFOP012 Data Resource Management Operators

Provisions the data resources that ODA Components declare in a `DataResourceConfig` (`oda.tmforum.org/v1alpha1`,
[ADR-010](https://github.com/hw-irc-sni/ai-canvas-architecture/blob/adr-tmfc060-knowledge/decision-log/ADR-010-data-resource-config-refinement.md))
when the data is served by another ODA Component in the Canvas (`spec.provider: odaComponent`). The first such
provider is TMFC060 Knowledge Management, which serves knowledge-bases and ontology modules.

- **Provider role:** a provider component declares the stores it serves (`providedDataResources`); the operator
  mirrors their contents (knowledge-bases, modules) into the DataResourceConfig status, where Resource Inventory
  picks them up.
- **Consumer role:** a consumer component declares the data it depends on (`dependentDataResources` with a
  `providerRef`); the operator ensures the knowledge-base on the provider, grants the consumer the provider's client
  roles, and hands it a binding Secret and a DependentAPI.

DataResourceConfigs of other providers (for example `databricks`) are left to their own operators: TMFOP012 never
writes to them.

At present, there is one implementation:

* [Data Resource Operator](./dataresource-operator/): kopf operator, deployed by
  [`charts/dataresource-operator`](../../../charts/dataresource-operator) through `canvas-oda`
  (`dataresource-operator.enabled`, off by default).
