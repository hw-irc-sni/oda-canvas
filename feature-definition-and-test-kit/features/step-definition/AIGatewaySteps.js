// Steps for the registration of MCP APIs with the Canvas AI gateway, used by UC018.
const aiGatewayUtils = require('ai-gateway-utils-agentgateway');

const { Then } = require('@cucumber/cucumber');
const assert = require('assert');

const REGISTRATION_TIMEOUT = 180 * 1000; // 3 minutes for the Canvas to register or remove a target
const POLL_INTERVAL = 5 * 1000;
const TIMEOUT_BUFFER = 5 * 1000;

/**
 * Polls check() until it returns a truthy value or the timeout expires.
 */
async function waitFor(check, description) {
  const deadline = Date.now() + REGISTRATION_TIMEOUT;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const result = await check();
      if (result) return result;
    } catch (error) {
      lastError = error;
    }
    await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL));
  }
  assert.fail(`Timed out after ${REGISTRATION_TIMEOUT / 1000}s waiting for ${description}` +
    (lastError ? ` (last error: ${lastError.message})` : ''));
}

/**
 * Step: the AI gateway has an MCP target for the ExposedAPI '<name>' in namespace '<namespace>'
 */
Then('the AI gateway has an MCP target for the ExposedAPI {string} in namespace {string}', { timeout: REGISTRATION_TIMEOUT + TIMEOUT_BUFFER }, async function (exposedAPIName, namespace) {
  const targetName = aiGatewayUtils.mcpTargetNameForExposedAPI(namespace, exposedAPIName);
  console.log(`\n=== Waiting for MCP target '${targetName}' in the AI gateway ===`);
  await waitFor(async () => (await aiGatewayUtils.getMcpTargets()).some(t => t.name === targetName),
    `MCP target '${targetName}' in the AI gateway`);
  console.log(`✅ MCP target '${targetName}' is registered`);
});

/**
 * Step: the AI gateway has no MCP target for the ExposedAPI '<name>' in namespace '<namespace>'
 */
Then('the AI gateway has no MCP target for the ExposedAPI {string} in namespace {string}', { timeout: REGISTRATION_TIMEOUT + TIMEOUT_BUFFER }, async function (exposedAPIName, namespace) {
  const targetName = aiGatewayUtils.mcpTargetNameForExposedAPI(namespace, exposedAPIName);
  console.log(`\n=== Waiting for MCP target '${targetName}' to be removed from the AI gateway ===`);
  await waitFor(async () => !(await aiGatewayUtils.getMcpTargets()).some(t => t.name === targetName),
    `MCP target '${targetName}' to be removed from the AI gateway`);
  console.log(`✅ MCP target '${targetName}' is not registered`);
});

/**
 * Step: the tools of the MCP target for the ExposedAPI '<name>' in namespace '<namespace>' can be
 * listed through the AI gateway
 */
Then('the tools of the MCP target for the ExposedAPI {string} in namespace {string} can be listed through the AI gateway', { timeout: REGISTRATION_TIMEOUT + TIMEOUT_BUFFER }, async function (exposedAPIName, namespace) {
  const targetName = aiGatewayUtils.mcpTargetNameForExposedAPI(namespace, exposedAPIName);
  console.log(`\n=== Listing the tools of MCP target '${targetName}' through the AI gateway ===`);
  const tools = await waitFor(async () => {
    const found = await aiGatewayUtils.listToolsOfTarget(targetName);
    return found.length ? found : null;
  }, `tools of MCP target '${targetName}' through the AI gateway`);
  console.log(`✅ ${tools.length} tools: ${tools.map(t => t.name).join(', ')}`);
});
