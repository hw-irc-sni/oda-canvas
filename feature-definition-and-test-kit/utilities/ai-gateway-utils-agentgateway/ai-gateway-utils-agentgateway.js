// AI gateway utilities for agentgateway, used by the BDD steps in AIGatewaySteps.js.
// Swap this package to test another AI gateway implementation.
//
// agentgateway keeps its MCP targets in a ConfigMap (config.yaml -> mcp.targets[], each
// {name, mcp: {host}}). Through the gateway's MCP endpoint, the tools of a target are listed
// with the target name as a prefix: '<target>_<tool>'.
const k8s = require('@kubernetes/client-node')
const axios = require('axios')
const YAML = require('yaml')

const kc = new k8s.KubeConfig()
kc.loadFromDefault()

const GATEWAY_NAMESPACE = process.env.AI_GATEWAY_NAMESPACE || 'canvas'
const GATEWAY_CONFIGMAP = process.env.AI_GATEWAY_CONFIGMAP || 'agentgateway-config'
const GATEWAY_CONFIG_KEY = process.env.AI_GATEWAY_CONFIG_KEY || 'config.yaml'
const GATEWAY_MCP_URL = process.env.AI_GATEWAY_MCP_URL || 'http://localhost:8080/mcp'

/**
 * Parses an MCP JSON-RPC response that is either plain JSON or server-sent events.
 * @param {String|Object} body - the HTTP response body
 * @return {Object} the JSON-RPC message
 */
function parseMcpResponse(body) {
  if (typeof body === 'object') return body
  const dataLines = String(body).split('\n').filter(line => line.startsWith('data:'))
  const payload = dataLines.length ? dataLines[dataLines.length - 1].slice(5).trim() : body
  return JSON.parse(payload)
}

const aiGatewayUtils = {

  /**
   * Name of the MCP target that the Canvas registers for an MCP ExposedAPI.
   * Canvas-owned targets are prefixed 'oda-<namespace>-' so they never clash with targets added by hand.
   * @param {String} inNamespace       Namespace of the ExposedAPI
   * @param {String} inExposedAPIName  Name of the ExposedAPI
   * @return {String} the target name
   */
  mcpTargetNameForExposedAPI: function (inNamespace, inExposedAPIName) {
    return `oda-${inNamespace}-${inExposedAPIName}`
  },

  /**
   * Returns the MCP targets configured in the AI gateway.
   * @return {Array} the targets, each with at least a name
   */
  getMcpTargets: async function () {
    const k8sCoreApi = kc.makeApiClient(k8s.CoreV1Api)
    const response = await k8sCoreApi.readNamespacedConfigMap(GATEWAY_CONFIGMAP, GATEWAY_NAMESPACE)
    const config = YAML.parse((response.body.data || {})[GATEWAY_CONFIG_KEY] || '') || {}
    const mcpSections = []
    const walk = (node) => {
      if (Array.isArray(node)) node.forEach(walk)
      else if (node && typeof node === 'object') {
        if (node.mcp && Array.isArray(node.mcp.targets)) mcpSections.push(node.mcp)
        Object.values(node).forEach(walk)
      }
    }
    walk(config)
    return mcpSections.flatMap(section => section.targets).filter(target => target && target.name)
  },

  /**
   * Lists the tools exposed through the AI gateway's MCP endpoint.
   * @return {Array} the tools, each with a name
   */
  listToolsThroughGateway: async function () {
    const response = await axios.post(GATEWAY_MCP_URL,
      { jsonrpc: '2.0', id: 1, method: 'tools/list', params: {} },
      {
        headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
        transformResponse: [data => data]
      })
    const message = parseMcpResponse(response.data)
    if (message.error) throw new Error(`tools/list failed: ${JSON.stringify(message.error)}`)
    return (message.result && message.result.tools) || []
  },

  /**
   * Lists the tools of one MCP target, as seen through the AI gateway.
   * @param {String} targetName  Name of the MCP target
   * @return {Array} the tools of that target
   */
  listToolsOfTarget: async function (targetName) {
    const tools = await this.listToolsThroughGateway()
    return tools.filter(tool => tool.name && tool.name.startsWith(`${targetName}_`))
  }
}

module.exports = aiGatewayUtils
