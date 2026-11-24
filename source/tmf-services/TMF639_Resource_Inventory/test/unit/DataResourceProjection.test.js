// DataResource projection of DataResourceConfigs (ADR-010, ADR-011): store and content entries, relationships,
// the componentId characteristic and the status mapping.
const { expect } = require('chai');
const sinon = require('sinon');

const KubernetesResourceService = require('../../services/KubernetesResourceService');

const provider = (status = {}) => ({
  metadata: { name: 'km-knowledgemanagement', namespace: 'components', resourceVersion: '7',
              creationTimestamp: '2026-11-24T00:00:00Z',
              labels: { 'oda.tmforum.org/componentName': 'km-knowledgemanagement' } },
  spec: { provider: 'odaComponent', role: 'provider', providedDataResources: [
    { name: 'knowledge-vectors', type: 'vectorStore', engine: 'qdrant', description: 'Vectors',
      exposedAPI: 'knowledgemanagement', mcpAPI: 'knowledgemcp' },
    { name: 'knowledge-graph', type: 'knowledgeGraph', engine: 'pyoxigraph',
      exposedAPI: 'knowledgemanagement', mcpAPI: 'knowledgemcp' }] },
  status
});

const mirrored = {
  conditions: [{ type: 'Ready', status: 'True' }],
  providedResources: [
    { kind: 'knowledge', id: 'incident-kb', name: 'Incidents', storeKind: 'hybrid', lifecycleStatus: 'published',
      href: 'https://gw/km/knowledge/incident-kb' },
    { kind: 'knowledge', id: 'graph-kb', name: 'Graph', storeKind: 'graph', lifecycleStatus: 'draft', href: 'h' },
    { kind: 'knowledge', id: 'vec-kb', name: 'Vec', storeKind: 'vector', lifecycleStatus: 'deprecated', href: 'h' },
    { kind: 'knowledgeSpecification', id: 'tio-core', name: 'TIO core', type: 'knowledgeGraph', layer: 'Telecom',
      lifecycleStatus: 'rejected', href: 'h' }]
};

const consumer = {
  metadata: { name: 'agent-knowledge', namespace: 'components', labels: { 'oda.tmforum.org/componentName': 'agent' } },
  spec: { provider: 'odaComponent', dependentDataResources: [
    { name: 'incident-kb', type: 'vectorStore', providerRef: { componentId: 'TMFC060' } },
    { name: 'pending', type: 'knowledgeGraph', providerRef: { componentId: 'TMFC060' } }] },
  status: { bindings: [
    { name: 'incident-kb', providerComponent: 'km-knowledgemanagement', kbId: 'incident-kb', ready: true },
    { name: 'pending', providerComponent: 'km-knowledgemanagement', ready: false }] }
};

const databricks = {
  metadata: { name: 'telco-data', namespace: 'components', labels: { 'oda.tmforum.org/componentName': 'analytics' } },
  spec: { provider: 'databricks', workspace: { host: 'https://w' }, dependentDataResources: [
    { name: 'kpis', accessType: 'sql', dataProductType: 'table' },
    { name: 'incidents', accessType: 'vector-search' },
    { name: 'typed', type: 'cache', accessType: 'genie' }] },
  status: { phase: 'Ready' }
};

const rel = (resource, type) => resource.resourceRelationship.filter(r => r.relationshipType === type)
  .map(r => r.resource.id).sort();
const chars = resource => Object.fromEntries(resource.resourceCharacteristic.map(c => [c.name, c.value]));

describe('DataResource projection (ADR-011)', () => {
  let service;
  beforeEach(() => { service = new KubernetesResourceService(); });

  it('projects the provider stores with providedBy and contains', () => {
    const { resources } = service.projectDataResources([provider(mirrored)]);
    const vectors = resources.find(r => r.id === 'km-knowledgemanagement.knowledge-vectors');
    expect(vectors.category).to.equal('DataResource');
    expect(vectors.resourceSpecification).to.deep.equal({ '@type': 'ResourceSpecificationRef', id: 'DataResource', name: 'vectorStore' });
    expect(chars(vectors)).to.deep.equal({ type: 'vectorStore', engine: 'qdrant', namespace: 'components',
      dataResourceConfig: 'km-knowledgemanagement', description: 'Vectors' });
    expect(rel(vectors, 'providedBy')).to.deep.equal(['km-knowledgemanagement']);
    expect(rel(vectors, 'contains')).to.deep.equal(['km-knowledgemanagement.knowledge.incident-kb',
      'km-knowledgemanagement.knowledge.vec-kb']);
    const graph = resources.find(r => r.id === 'km-knowledgemanagement.knowledge-graph');
    expect(rel(graph, 'contains')).to.deep.equal(['km-knowledgemanagement.knowledge.graph-kb',
      'km-knowledgemanagement.knowledge.incident-kb', 'km-knowledgemanagement.knowledgeSpecification.tio-core']);
    expect([vectors.resourceStatus, vectors.operationalState]).to.deep.equal(['available', 'enable']);
  });

  it('projects content entries with isPartOf the stores that hold them', () => {
    const { resources } = service.projectDataResources([provider(mirrored)]);
    const byId = id => resources.find(r => r.id === `km-knowledgemanagement.${id}`);
    const kb = byId('knowledge.incident-kb');
    expect(kb.name).to.equal('Incidents');
    expect(chars(kb)).to.deep.equal({ kind: 'knowledge', storeKind: 'hybrid', lifecycleStatus: 'published',
      namespace: 'components', href: 'https://gw/km/knowledge/incident-kb' });
    expect(rel(kb, 'isPartOf')).to.deep.equal(['km-knowledgemanagement.knowledge-graph', 'km-knowledgemanagement.knowledge-vectors']);
    expect(rel(kb, 'providedBy')).to.deep.equal(['km-knowledgemanagement']);
    expect(rel(byId('knowledge.graph-kb'), 'isPartOf')).to.deep.equal(['km-knowledgemanagement.knowledge-graph']);
    expect(rel(byId('knowledge.vec-kb'), 'isPartOf')).to.deep.equal(['km-knowledgemanagement.knowledge-vectors']);
    const mod = byId('knowledgeSpecification.tio-core');
    expect(chars(mod).layer).to.equal('Telecom');
    expect(rel(mod, 'isPartOf')).to.deep.equal(['km-knowledgemanagement.knowledge-graph']);
  });

  it('maps lifecycleStatus and the Ready condition to resource and operational states', () => {
    const { resources } = service.projectDataResources([provider(mirrored)]);
    const state = id => { const r = resources.find(x => x.id === `km-knowledgemanagement.${id}`); return [r.resourceStatus, r.operationalState]; };
    expect(state('knowledge.incident-kb')).to.deep.equal(['available', 'enable']);
    expect(state('knowledge.graph-kb')).to.deep.equal(['standby', 'disable']);
    expect(state('knowledge.vec-kb')).to.deep.equal(['suspended', 'disable']);
    expect(state('knowledgeSpecification.tio-core')).to.deep.equal(['unknown', 'disable']);
    const notReady = service.projectDataResources([provider({ conditions: [{ type: 'Ready', status: 'False' }] })]);
    expect(notReady.resources.map(r => r.resourceStatus)).to.deep.equal(['standby', 'standby']);
  });

  it('gives the provider component provides, and consumers dependsOn their bindings', () => {
    const { componentRelationships } = service.projectDataResources([provider(mirrored), consumer]);
    const provides = componentRelationships['km-knowledgemanagement'].filter(r => r.relationshipType === 'provides');
    expect(provides).to.have.length(6);
    expect(componentRelationships.agent.map(r => [r.relationshipType, r.resource.id])).to.deep.equal([
      ['dependsOn', 'km-knowledgemanagement.knowledge.incident-kb'],
      ['dependsOn', 'km-knowledgemanagement.knowledge-graph']]);   // no KB bound yet: the store of its type
  });

  it('projects Databricks items as stores the consumer dependsOn', () => {
    const { resources, componentRelationships } = service.projectDataResources([databricks]);
    expect(resources.map(r => [r.id, chars(r).type, chars(r).engine, r.resourceStatus])).to.deep.equal([
      ['telco-data.kpis', 'dataProduct', 'databricks', 'available'],
      ['telco-data.incidents', 'vectorStore', 'databricks', 'available'],
      ['telco-data.typed', 'cache', 'databricks', 'available']]);
    expect(resources.every(r => r.resourceRelationship.length === 0)).to.equal(true);
    expect(componentRelationships.analytics.map(r => r.resource.id)).to.deep.equal(
      ['telco-data.kpis', 'telco-data.incidents', 'telco-data.typed']);
  });

  it('adds componentId and the data relationships to the ODAComponent', () => {
    const component = { metadata: { name: 'km-knowledgemanagement', namespace: 'components' },
      spec: { componentMetadata: { id: 'TMFC060', name: 'KnowledgeManagement' } }, status: {} };
    const { componentRelationships } = service.projectDataResources([provider(mirrored)]);
    const resource = service.convertComponentToResource(component, [], componentRelationships['km-knowledgemanagement']);
    expect(chars(resource).componentId).to.equal('TMFC060');
    expect(chars(resource).id).to.equal('TMFC060');
    expect(rel(resource, 'provides')).to.include('km-knowledgemanagement.knowledge-vectors');
  });

  it('lists DataResources and gets them by id, and works without the CRD', async () => {
    const list = sinon.stub(service.customObjectsApi, 'listNamespacedCustomObject');
    list.withArgs('oda.tmforum.org', 'v1', 'components', 'components').resolves({ body: { items: [] } });
    list.withArgs('oda.tmforum.org', 'v1', 'components', 'exposedapis').resolves({ body: { items: [] } });
    list.withArgs('oda.tmforum.org', 'v1alpha1', 'components', 'dataresourceconfigs')
      .resolves({ body: { items: [provider(mirrored)] } });
    sinon.stub(service.customObjectsApi, 'getNamespacedCustomObject').rejects({ response: { statusCode: 404 } });
    const all = await service.listResources('components');
    expect(all.filter(r => r.category === 'DataResource')).to.have.length(6);
    const one = await service.getResourceById('km-knowledgemanagement.knowledge.incident-kb', 'components');
    expect(one.name).to.equal('Incidents');
    list.withArgs('oda.tmforum.org', 'v1alpha1', 'components', 'dataresourceconfigs')
      .rejects({ response: { statusCode: 404 } });
    expect(await service.getDataResourceConfigs('components')).to.deep.equal([]);
  });
});

describe('Attribute filters of listResource (?category=DataResource)', () => {
  const { applyAttributeFilters } = require('../../services/ResourceService');
  const resources = [
    { id: 'a', category: 'DataResource', resourceStatus: 'available' },
    { id: 'b', category: 'ODAComponent', resourceStatus: 'available' },
    { id: 'c', category: 'API', resourceStatus: 'standby' }];

  it('keeps the resources whose attribute equals the value', () => {
    expect(applyAttributeFilters(resources, { category: 'DataResource' }).map(r => r.id)).to.deep.equal(['a']);
  });
  it('treats a comma as alternatives and combines parameters', () => {
    expect(applyAttributeFilters(resources, { category: 'DataResource,API' }).map(r => r.id)).to.deep.equal(['a', 'c']);
    expect(applyAttributeFilters(resources, { category: 'DataResource,API', resourceStatus: 'standby' })
      .map(r => r.id)).to.deep.equal(['c']);
  });
  it('ignores the reserved parameters and unknown attributes', () => {
    expect(applyAttributeFilters(resources, { namespace: 'x', fields: 'id', limit: '1', nosuch: 'y' })).to.have.length(3);
  });
});
