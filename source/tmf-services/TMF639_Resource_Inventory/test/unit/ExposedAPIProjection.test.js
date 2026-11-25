// ExposedAPI projection: the characteristics in-cluster clients need to call the implementation Service directly
const { expect } = require('chai');

const KubernetesResourceService = require('../../services/KubernetesResourceService');

const exposedAPI = (spec = {}) => ({
  metadata: { name: 'km-knowledgemanagement-knowledgemanagement', namespace: 'components', resourceVersion: '3',
              labels: { 'oda.tmforum.org/componentName': 'km-knowledgemanagement' } },
  spec: { name: 'knowledgemanagement', apiType: 'openapi', implementation: 'km-kmapi', ...spec },
  status: { apiStatus: { url: 'https://127.0.0.1/km-knowledgemanagement/tmf-api/knowledgeManagement/v5' },
            implementation: { ready: true } }
});
const chars = resource => Object.fromEntries(resource.resourceCharacteristic.map(c => [c.name, c.value]));

describe('ExposedAPI projection', () => {
  let service;
  beforeEach(() => { service = new KubernetesResourceService(); });

  it('reports the implementation Service, port and path next to the gateway url', () => {
    const resource = service.convertExposedAPIToResource(
      exposedAPI({ port: 8080, path: '/km-knowledgemanagement/tmf-api/knowledgeManagement/v5' }));
    expect(chars(resource)).to.include({
      apiName: 'knowledgemanagement', apiType: 'openapi', implementation: 'km-kmapi', port: 8080,
      path: '/km-knowledgemanagement/tmf-api/knowledgeManagement/v5',
      url: 'https://127.0.0.1/km-knowledgemanagement/tmf-api/knowledgeManagement/v5'
    });
    expect(resource.category).to.equal('API');
  });

  it('omits port and path when the ExposedAPI has none', () => {
    const names = service.convertExposedAPIToResource(exposedAPI()).resourceCharacteristic.map(c => c.name);
    expect(names).to.not.include.members(['port']);
    expect(names).to.not.include('path');
  });
});
