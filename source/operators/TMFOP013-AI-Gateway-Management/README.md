# TMFOP013 AI Gateway Management Operators

Connects ODA Components to the Canvas AI gateway (the Interaction Gateway of ADR-004). When a component exposes an
MCP API (an ExposedAPI with `apiType: mcp`), the operator registers it with the gateway, so agents find its tools
through one MCP endpoint; when the component goes, the registration goes too.

At present, there is one implementation:

* [agentgateway MCP Operator](./agentgateway-mcp-operator/): kopf operator for agentgateway in standalone mode
  ([`charts/agentgateway`](../../../charts/agentgateway)), deployed by
  [`charts/agentgateway-mcp-operator`](../../../charts/agentgateway-mcp-operator) through `canvas-oda`
  (`agentgateway-mcp-operator.enabled`, off by default).
