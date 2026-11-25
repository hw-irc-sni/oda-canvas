// Steps for data resources declared with DataResourceConfig (ADR-010), used by UC018.
const resourceInventoryUtils = require('resource-inventory-utils-kubernetes');
const identityManagerUtils = require('identity-manager-utils-keycloak');

const { Given, When, Then } = require('@cucumber/cucumber');
const assert = require('assert');

const NAMESPACE = 'components';
const BINDING_TIMEOUT = 300 * 1000; // 5 minutes for the operator to bind or unbind
const POLL_INTERVAL = 5 * 1000;
const TIMEOUT_BUFFER = 5 * 1000;
const DEBUG_LOGS = false; // set to true for verbose debugging

/**
 * Polls check() until it returns a truthy value or the timeout expires.
 * @param {Function} check - async function returning a truthy value when the condition holds
 * @param {String} description - what is awaited, for the error message
 * @return {*} the truthy value returned by check()
 */
async function waitFor(check, description) {
  const deadline = Date.now() + BINDING_TIMEOUT;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const result = await check();
      if (result) return result;
    } catch (error) {
      lastError = error;
      if (DEBUG_LOGS) console.log(`Waiting for ${description}: ${error.message}`);
    }
    await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL));
  }
  assert.fail(`Timed out after ${BINDING_TIMEOUT / 1000}s waiting for ${description}` +
    (lastError ? ` (last error: ${lastError.message})` : ''));
}

/**
 * Step: I create a consumer DataResourceConfig '<name>' for the '<consumer>' component with the
 * dependency '<item>' on component '<componentId>'
 *
 * The dependency asks the provider to create a hybrid knowledge-base named after the item.
 */
Given('I create a consumer DataResourceConfig {string} for the {string} component with the dependency {string} on component {string}', async function (name, consumerComponent, itemName, providerComponentId) {
  console.log(`\n=== Creating consumer DataResourceConfig '${name}' for '${consumerComponent}' ===`);
  const spec = {
    provider: 'odaComponent',
    role: 'consumer',
    dependentDataResources: [{
      name: itemName,
      type: 'vectorStore',
      required: true,
      access: 'read',
      providerRef: {
        componentId: providerComponentId,
        create: { name: itemName, storeKind: 'hybrid' }
      }
    }]
  };
  const labels = { 'oda.tmforum.org/componentName': consumerComponent };
  await resourceInventoryUtils.createDataResourceConfig(name, NAMESPACE, labels, spec);
  console.log(`✅ Created DataResourceConfig '${name}'`);
});

/**
 * Step: I delete the DataResourceConfig '<name>'
 */
When('I delete the DataResourceConfig {string}', async function (name) {
  console.log(`\n=== Deleting DataResourceConfig '${name}' ===`);
  await resourceInventoryUtils.deleteDataResourceConfig(name, NAMESPACE);
  console.log(`✅ Deleted DataResourceConfig '${name}'`);
});

/**
 * Step: the DataResourceConfig '<name>' has the condition '<type>' with status '<status>'
 */
Then('the DataResourceConfig {string} has the condition {string} with status {string}', { timeout: BINDING_TIMEOUT + TIMEOUT_BUFFER }, async function (name, conditionType, conditionStatus) {
  console.log(`\n=== Waiting for DataResourceConfig '${name}' condition ${conditionType}=${conditionStatus} ===`);
  await waitFor(async () => {
    const drc = await resourceInventoryUtils.getDataResourceConfig(name, NAMESPACE);
    const conditions = (drc && drc.status && drc.status.conditions) || [];
    return conditions.some(c => c.type === conditionType && c.status === conditionStatus);
  }, `condition ${conditionType}=${conditionStatus} on DataResourceConfig '${name}'`);
  console.log(`✅ ${conditionType}=${conditionStatus}`);
});

/**
 * Step: the Secret '<name>' has the keys:
 * | key |
 */
Then('the Secret {string} has the keys:', { timeout: BINDING_TIMEOUT + TIMEOUT_BUFFER }, async function (secretName, dataTable) {
  const expectedKeys = dataTable.hashes().map(row => row.key);
  console.log(`\n=== Waiting for Secret '${secretName}' with keys ${expectedKeys.join(', ')} ===`);
  const secret = await waitFor(() => resourceInventoryUtils.getSecret(secretName, NAMESPACE), `Secret '${secretName}'`);
  const actualKeys = Object.keys(secret.data || {});
  const missing = expectedKeys.filter(key => !actualKeys.includes(key));
  assert.deepStrictEqual(missing, [], `Secret '${secretName}' is missing the keys ${missing.join(', ')} (it has ${actualKeys.join(', ')})`);
  console.log(`✅ Secret '${secretName}' has ${expectedKeys.join(', ')}`);
});

/**
 * Step: the Secret '<name>' does not exist
 */
Then('the Secret {string} does not exist', { timeout: BINDING_TIMEOUT + TIMEOUT_BUFFER }, async function (secretName) {
  console.log(`\n=== Waiting for Secret '${secretName}' to be removed ===`);
  await waitFor(async () => (await resourceInventoryUtils.getSecret(secretName, NAMESPACE)) === null, `Secret '${secretName}' to be removed`);
  console.log(`✅ Secret '${secretName}' does not exist`);
});

/**
 * Step: a DependentAPI owned by the DataResourceConfig '<name>' exists
 */
Then('a DependentAPI owned by the DataResourceConfig {string} exists', { timeout: BINDING_TIMEOUT + TIMEOUT_BUFFER }, async function (name) {
  console.log(`\n=== Waiting for a DependentAPI owned by DataResourceConfig '${name}' ===`);
  const owned = await waitFor(async () => {
    const items = await resourceInventoryUtils.getDependentAPIsOwnedBy('DataResourceConfig', name, NAMESPACE);
    return items.length > 0 ? items : null;
  }, `a DependentAPI owned by DataResourceConfig '${name}'`);
  console.log(`✅ ${owned.map(d => d.metadata.name).join(', ')}`);
});

/**
 * Step: the client '<consumer>' has the role '<role>' of the client '<provider>'
 */
Then('the client {string} has the role {string} of the client {string}', { timeout: BINDING_TIMEOUT + TIMEOUT_BUFFER }, async function (consumerClient, role, providerClient) {
  console.log(`\n=== Waiting for client '${consumerClient}' to hold role '${role}' of '${providerClient}' ===`);
  await waitFor(async () => {
    const roles = await identityManagerUtils.getClientRolesOfServiceAccount(consumerClient, providerClient);
    return roles.includes(role);
  }, `role '${role}' of '${providerClient}' on client '${consumerClient}'`);
  console.log(`✅ '${consumerClient}' holds '${role}'`);
});

/**
 * Step: the client '<consumer>' does not have the role '<role>' of the client '<provider>'
 */
Then('the client {string} does not have the role {string} of the client {string}', { timeout: BINDING_TIMEOUT + TIMEOUT_BUFFER }, async function (consumerClient, role, providerClient) {
  console.log(`\n=== Waiting for role '${role}' of '${providerClient}' to be revoked from '${consumerClient}' ===`);
  await waitFor(async () => {
    const roles = await identityManagerUtils.getClientRolesOfServiceAccount(consumerClient, providerClient);
    return !roles.includes(role);
  }, `role '${role}' of '${providerClient}' to be revoked from '${consumerClient}'`);
  console.log(`✅ '${consumerClient}' no longer holds '${role}'`);
});
