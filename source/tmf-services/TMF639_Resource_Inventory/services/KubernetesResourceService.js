const k8s = require('@kubernetes/client-node');
const logger = require('../logger');
const config = require('../config');

class KubernetesResourceService {
    constructor() {
        this.k8sConfig = new k8s.KubeConfig();
        this.init();
    }

    init() {
        try {
            // Load config from cluster (in-cluster config when running in pod)
            // or from local kubeconfig file
            if (process.env.KUBERNETES_SERVICE_HOST) {
                this.k8sConfig.loadFromCluster();
            } else {
                this.k8sConfig.loadFromDefault();
            }
            
            this.customObjectsApi = this.k8sConfig.makeApiClient(k8s.CustomObjectsApi);
            this.coreApi = this.k8sConfig.makeApiClient(k8s.CoreV1Api);
            
            logger.info('Kubernetes client initialized successfully');
        } catch (error) {
            logger.error('Failed to initialize Kubernetes client:', error);
            throw error;
        }
    }

    /**
     * Get ODA Components from Kubernetes
     */
    async getComponents(namespace = null) {
        try {
			namespace = namespace || config.KUBERNETES_NAMESPACE;
			logger.info(`Fetching Components from namespace: ${namespace}`);
            const response = await this.customObjectsApi.listNamespacedCustomObject(
                'oda.tmforum.org',     // group
                'v1',             // version  
                namespace,             // namespace
                'components'           // plural
            );
            
            return response.body.items || [];
        } catch (error) {
            logger.error('Error fetching Components:', error);
            throw error;
        }
    }

    /**
     * Get specific ODA Component by name
     */
    async getComponent(name, namespace = null) {
        try {
			namespace = namespace || config.KUBERNETES_NAMESPACE;
			logger.info(`Fetching Components from namespace: ${namespace}`);
            const response = await this.customObjectsApi.getNamespacedCustomObject(
                'oda.tmforum.org',     // group
                'v1',             // version
                namespace,             // namespace
                'components',          // plural
                name                   // name
            );
            
            return response.body;
        } catch (error) {
            if (error.response && error.response.statusCode === 404) {
                return null;
            }
            logger.error(`Error fetching Component ${name}:`, error);
            throw error;
        }
    }

    /**
     * Get ExposedAPIs from Kubernetes
     */
    async getExposedAPIs(namespace = null) {
        try {
			namespace = namespace || config.KUBERNETES_NAMESPACE;
			logger.info(`Fetching ExposedAPIs from namespace: ${namespace}`);
            const response = await this.customObjectsApi.listNamespacedCustomObject(
                'oda.tmforum.org',     // group
                'v1',             // version
                namespace,             // namespace
                'exposedapis'          // plural
            );
            
            return response.body.items || [];
        } catch (error) {
            logger.error('Error fetching ExposedAPIs:', error);
            throw error;
        }
    }

    /**
     * Get specific ExposedAPI by name
     */
    async getExposedAPI(name, namespace = config.KUBERNETES_NAMESPACE) {
        try {
            const response = await this.customObjectsApi.getNamespacedCustomObject(
                'oda.tmforum.org',     // group
                'v1',             // version
                namespace,             // namespace
                'exposedapis',         // plural
                name                   // name
            );
            
            return response.body;
        } catch (error) {
            if (error.response && error.response.statusCode === 404) {
                return null;
            }
            logger.error(`Error fetching ExposedAPI ${name}:`, error);
            throw error;
        }
    }

    /**
     * Find ExposedAPIs that belong to a specific Component based on oda.tmforum.org/componentName label
     */
    findRelatedExposedAPIs(componentName, exposedAPIs) {
        return exposedAPIs.filter(api => {
            const labels = api.metadata?.labels || {};
            return labels['oda.tmforum.org/componentName'] === componentName;
        });
    }

    /**
     * Find Component that owns a specific ExposedAPI based on oda.tmforum.org/componentName label
     */
    findRelatedComponent(exposedAPI, components) {
        const labels = exposedAPI.metadata?.labels || {};
        const componentName = labels['oda.tmforum.org/componentName'];
        
        if (!componentName) {
            return null;
        }
        
        return components.find(component => component.metadata?.name === componentName) || null;
    }

    /**
     * Convert Kubernetes Component to TMF639 Resource format
     */
    convertComponentToResource(k8sComponent, relatedExposedAPIs = [], dataRelationships = []) {
        const metadata = k8sComponent.metadata || {};
        const spec = k8sComponent.spec || {};
        const status = k8sComponent.status || {};

        // Build base characteristics
        const characteristics = [
            {
                '@type': 'Characteristic',
                name: 'namespace',
                value: metadata.namespace
            },
            {
                '@type': 'Characteristic',
                name: 'deploymentStatus',
                value: status["summary/status"]?.deployment_status || 'Unknown'
            }
        ];

        // Add all properties from spec.componentMetadata as characteristics
        if (spec.componentMetadata && typeof spec.componentMetadata === 'object') {
            Object.entries(spec.componentMetadata).forEach(([key, value]) => {
                let characteristicValue;
                
                if (Array.isArray(value)) {
                    // Keep arrays as arrays
                    characteristicValue = [...value];
                } else if (typeof value === 'object' && value !== null) {
                    // JSON stringify other objects
                    characteristicValue = JSON.stringify(value);
                } else {
                    // Keep primitive values as-is
                    characteristicValue = value;
                }
                
                characteristics.push({
                    '@type': 'Characteristic',
                    name: key,
                    value: characteristicValue
                });
            });
        }
        // The component's catalog id under an unambiguous name (ADR-011); the 'id' characteristic stays
        if (spec.componentMetadata && spec.componentMetadata.id !== undefined) {
            characteristics.push({
                '@type': 'Characteristic',
                name: 'componentId',
                value: spec.componentMetadata.id
            });
        }
        // Build related resources - ExposedAPIs that belong to this Component
        const resourceRelationship = relatedExposedAPIs.map(api => ({
			'@type': 'ResourceRelationship',
			relationshipType: 'exposes',
			id: `expby_${api.metadata.name}`,
			resource: {
				'@type': 'ResourceRef',
			    id: api.metadata.name,
			    href: `/tmf-api/resourceInventoryManagement/v5/resource/${api.metadata.name}`,
			}
        }));
        resourceRelationship.push(...dataRelationships);

        return {
            id: metadata.name,
			resourceVersion: metadata.resourceVersion,			
            href: `/tmf-api/resourceInventoryManagement/v5/resource/${metadata.name}`,
            '@type': 'LogicalResource',
            '@baseType': 'Resource',
            name: metadata.name,
            description: spec.description || `ODA Component: ${metadata.name}`,
            category: 'ODAComponent',
            resourceSpecification: {
                '@type': 'ResourceSpecificationRef',
                id: 'ODAComponent',
                name: spec.type || 'ODA Component',
                version: spec.version
            },
            resourceCharacteristic: characteristics,
            resourceStatus: this.mapComponentStatusToResourceStatus(status),
            operationalState: this.mapComponentStatusToOperationalState(status),
            administrativeState: 'unlocked',
            usageState: 'active',
            startDate: metadata.creationTimestamp,
            // TODO[FH]: cauesed problems with schema validation
			//           OAS expects PlaceRef here and Namespace is not defined.
			//           As namespace info is already in characteristics, omitting for now.
			// place: [{
            //     id: metadata.namespace,
            //     name: metadata.namespace,
            //     '@type': 'Namespace'
            // }],
            resourceRelationship: resourceRelationship
        };
    }    /**
     * Convert Kubernetes ExposedAPI to TMF639 Resource format
     */
    convertExposedAPIToResource(k8sExposedAPI, relatedComponent = null) {
        const metadata = k8sExposedAPI.metadata || {};
        const spec = k8sExposedAPI.spec || {};
        const status = k8sExposedAPI.status || {};

        // Build base characteristics
        const characteristics = [
            {
                '@type': 'Characteristic',
                name: 'namespace',
                value: metadata.namespace
            },
            {
                '@type': 'Characteristic',
                name: 'apiName',
                value: spec.name
            },
            {
                '@type': 'Characteristic',
                name: 'apiType',
                value: spec.apiType
            },
            {
                '@type': 'Characteristic',
                name: 'url',
                value: status.apiStatus?.url
            },
            {
                '@type': 'Characteristic',
                name: 'implementation',
                value: spec.implementation
            }
        ];

		// Format was changed from array to flattened key-value pairs
        // // Add specification characteristic if spec.specification exists and is an array
        // if (spec.specification) && Array.isArray(spec.specification)) {
        //     characteristics.push({
        //         '@type': 'Characteristic',
        //         name: 'specification',
        //         value: [...spec.specification]
        //     });
		// } 
		if (spec.specification) {
	        characteristics.push({
                '@type': 'Characteristic',
                name: 'specification',
                value: spec.specification
            });
        }

        // Add apiDocs characteristic
        characteristics.push({
            '@type': 'Characteristic',
            name: 'apiDocs',
            value: status.apiStatus?.developerUI
        });        // Build related resources - Component that owns this ExposedAPI
        const resourceRelationship = [];
        if (relatedComponent) {
            resourceRelationship.push({
				'@type': 'ResourceRelationship',
				relationshipType: 'exposedBy',
				id: `expby_${relatedComponent.metadata.name}`,
				resource: {
					'@type': 'ResourceRef',
    	            id: relatedComponent.metadata.name,
        	        href: `/tmf-api/resourceInventoryManagement/v5/resource/${relatedComponent.metadata.name}`,
				}
            });
        }

        return {
            id: metadata.name,
			resourceVersion: metadata.resourceVersion,			
            href: `/tmf-api/resourceInventoryManagement/v5/resource/${metadata.name}`,
            '@type': 'LogicalResource',
            '@baseType': 'Resource',
            name: metadata.name,
            description: `Exposed API: ${spec.name || metadata.name}`,
            category: 'API',
            resourceSpecification: {
                '@type': 'ResourceSpecificationRef',
                id: 'API',
                name: 'API',
                version: spec.specification?.version
            },
            resourceCharacteristic: characteristics,
            resourceStatus: this.mapExposedAPIStatusToResourceStatus(status),
            operationalState: this.mapExposedAPIStatusToOperationalState(status),
            administrativeState: 'unlocked',
            usageState: 'active',
            startDate: metadata.creationTimestamp,
			// TODO[FH]: cauesed problems with schema validation
			//           OAS expects PlaceRef here and Namespace is not defined.
			//           As namespace info is already in characteristics, omitting for now.
            // place: [{
            //     id: metadata.namespace,
            //     name: metadata.namespace,
            //     '@type': 'Namespace'
            // }],
            resourceRelationship: resourceRelationship
        };
    }

    /**
     * Get DataResourceConfigs (ADR-010) from Kubernetes; an empty list when the CRD isn't installed
     */
    async getDataResourceConfigs(namespace = null) {
        try {
            namespace = namespace || config.KUBERNETES_NAMESPACE;
            const response = await this.customObjectsApi.listNamespacedCustomObject(
                'oda.tmforum.org', 'v1alpha1', namespace, 'dataresourceconfigs'
            );
            return response.body.items || [];
        } catch (error) {
            if (error.response && error.response.statusCode === 404) {
                return [];
            }
            logger.error('Error fetching DataResourceConfigs:', error);
            throw error;
        }
    }

    /**
     * Project DataResourceConfigs into DataResource resources at two levels (ADR-011):
     *  - store entries, from spec.providedDataResources[] (role provider) and Databricks dependentDataResources[]
     *  - content entries (knowledge-bases, ontology modules), from status.providedResources[]
     * and the relationships the ODAComponents get: provides (to what they serve) and dependsOn (to what they are
     * bound to).
     * @return {{resources: Array, componentRelationships: Object}} componentRelationships maps a component name to
     *         the relationships to add to its ODAComponent resource
     */
    projectDataResources(dataResourceConfigs = []) {
        const resources = [];
        const componentRelationships = {};
        const addToComponent = (component, relationshipType, targetId) => {
            if (!component) return;
            (componentRelationships[component] = componentRelationships[component] || [])
                .push(this.relationship(relationshipType, targetId));
        };
        // provider component name -> its provider DataResourceConfig, for the consumers' bindings
        const providerDrcByComponent = {};
        for (const drc of dataResourceConfigs) {
            const spec = drc.spec || {};
            if (spec.provider === 'odaComponent' && spec.role === 'provider') {
                providerDrcByComponent[this.componentOf(drc)] = drc;
            }
        }

        for (const drc of dataResourceConfigs) {
            const metadata = drc.metadata || {};
            const spec = drc.spec || {};
            const status = drc.status || {};
            const component = this.componentOf(drc);
            const role = spec.role || 'consumer';

            if (spec.provider === 'odaComponent' && role === 'provider') {
                const stores = (spec.providedDataResources || []).map(item => ({ item, id: `${metadata.name}.${item.name}` }));
                const contents = (status.providedResources || []).map(entry => ({
                    entry, id: `${metadata.name}.${entry.kind}.${entry.id}`, stores: this.storesOf(entry, stores)
                }));
                for (const { item, id } of stores) {
                    const relationships = component ? [this.relationship('providedBy', component)] : [];
                    contents.filter(c => c.stores.includes(id))
                        .forEach(c => relationships.push(this.relationship('contains', c.id)));
                    resources.push(this.dataResource(id, item.name, item.description, item.type, [
                        ['type', item.type], ['engine', item.engine], ['namespace', metadata.namespace],
                        ['dataResourceConfig', metadata.name], ['description', item.description]
                    ], relationships, this.storeStatus(status), metadata));
                    addToComponent(component, 'provides', id);
                }
                for (const { entry, id, stores: partOf } of contents) {
                    const relationships = component ? [this.relationship('providedBy', component)] : [];
                    partOf.forEach(storeId => relationships.push(this.relationship('isPartOf', storeId)));
                    resources.push(this.dataResource(id, entry.name || entry.id, undefined, entry.kind, [
                        ['kind', entry.kind], ['storeKind', entry.storeKind], ['layer', entry.layer],
                        ['lifecycleStatus', entry.lifecycleStatus], ['namespace', metadata.namespace],
                        ['href', entry.href]
                    ], relationships, this.contentStatus(entry.lifecycleStatus), metadata));
                    addToComponent(component, 'provides', id);
                }
            } else if (spec.provider === 'odaComponent') {
                // consumer: dependsOn the bound knowledge-base, or the provider's store when none is bound
                const items = spec.dependentDataResources || [];
                for (const binding of status.bindings || []) {
                    const providerDrc = providerDrcByComponent[binding.providerComponent];
                    if (!providerDrc) continue;
                    let target = null;
                    if (binding.kbId) {
                        target = `${providerDrc.metadata.name}.knowledge.${binding.kbId}`;
                    } else {
                        const wanted = (items.find(i => i.name === binding.name) || {}).type;
                        const provided = (providerDrc.spec.providedDataResources || []);
                        const store = provided.find(p => p.type === wanted) || provided[0];
                        if (store) target = `${providerDrc.metadata.name}.${store.name}`;
                    }
                    if (target) addToComponent(component, 'dependsOn', target);
                }
            } else if (spec.provider === 'databricks') {
                for (const item of spec.dependentDataResources || []) {
                    const id = `${metadata.name}.${item.name}`;
                    const type = item.type || (item.accessType === 'vector-search' ? 'vectorStore' : 'dataProduct');
                    resources.push(this.dataResource(id, item.name, item.description, type, [
                        ['type', type], ['engine', 'databricks'], ['namespace', metadata.namespace],
                        ['dataResourceConfig', metadata.name], ['description', item.description],
                        ['dataProductType', item.dataProductType], ['accessType', item.accessType]
                    ], [], this.storeStatus(status), metadata));
                    addToComponent(component, 'dependsOn', id);
                }
            }
        }
        return { resources, componentRelationships };
    }

    /** The ODA Component a DataResourceConfig belongs to: its componentName label, else a Component owner */
    componentOf(drc) {
        const metadata = drc.metadata || {};
        const label = (metadata.labels || {})['oda.tmforum.org/componentName'];
        if (label) return label;
        const owner = (metadata.ownerReferences || []).find(o => o.kind === 'Component');
        return owner ? owner.name : null;
    }

    /** The store entries a content entry is part of: a hybrid knowledge-base both stores, modules the graph */
    storesOf(entry, stores) {
        let wanted;
        if (entry.kind === 'knowledgeSpecification' || entry.storeKind === 'graph') wanted = ['knowledgeGraph'];
        else if (entry.storeKind === 'vector') wanted = ['vectorStore'];
        else if (entry.storeKind === 'hybrid') wanted = ['vectorStore', 'knowledgeGraph'];
        else wanted = entry.type ? [entry.type] : [];
        return stores.filter(s => wanted.includes(s.item.type)).map(s => s.id);
    }

    relationship(relationshipType, targetId) {
        return {
            '@type': 'ResourceRelationship',
            relationshipType,
            id: `${relationshipType}_${targetId}`,
            resource: {
                '@type': 'ResourceRef',
                id: targetId,
                href: `/tmf-api/resourceInventoryManagement/v5/resource/${targetId}`
            }
        };
    }

    dataResource(id, name, description, specName, characteristics, relationships, state, drcMetadata) {
        return {
            id,
            resourceVersion: drcMetadata.resourceVersion,
            href: `/tmf-api/resourceInventoryManagement/v5/resource/${id}`,
            '@type': 'LogicalResource',
            '@baseType': 'Resource',
            name,
            description: description || `Data resource: ${name}`,
            category: 'DataResource',
            resourceSpecification: {
                '@type': 'ResourceSpecificationRef',
                id: 'DataResource',
                name: specName || 'DataResource'
            },
            resourceCharacteristic: characteristics
                .filter(([, value]) => value !== undefined && value !== null && value !== '')
                .map(([cname, value]) => ({ '@type': 'Characteristic', name: cname, value })),
            resourceStatus: state.resourceStatus,
            operationalState: state.operationalState,
            administrativeState: 'unlocked',
            usageState: 'active',
            startDate: drcMetadata.creationTimestamp,
            resourceRelationship: relationships
        };
    }

    /** Store entries follow the DataResourceConfig's Ready condition (Databricks: its phase) */
    storeStatus(status = {}) {
        const ready = (status.conditions || []).find(c => c.type === 'Ready');
        const isReady = ready ? ready.status === 'True' : status.phase === 'Ready';
        return isReady ? { resourceStatus: 'available', operationalState: 'enable' }
            : { resourceStatus: 'standby', operationalState: 'disable' };
    }

    /** Content entries follow the item's lifecycleStatus */
    contentStatus(lifecycleStatus) {
        switch (lifecycleStatus) {
            case 'published':
                return { resourceStatus: 'available', operationalState: 'enable' };
            case 'deprecated':
                return { resourceStatus: 'suspended', operationalState: 'disable' };
            case 'rejected':
                return { resourceStatus: 'unknown', operationalState: 'disable' };
            default:   // draft, proposed, inReview, agreed
                return { resourceStatus: 'standby', operationalState: 'disable' };
        }
    }

    mapComponentStatusToResourceStatus(status) {
        const summary = status["summary/status"] || {};
        switch (summary.deployment_status) {
            case 'Complete':
                return 'available';
            case 'InProgress':
                return 'installing';
            case 'Failed':
                return 'reserved';
            default:
                return 'standby';
        }
    }

    mapComponentStatusToOperationalState(status) {
		const summary = status["summary/status"] || {};
        switch (summary.deployment_status) {
            case 'Complete':
                return 'enable';
            case 'InProgress':
                return 'enable';
            case 'Failed':
                return 'disable';
            default:
                return 'disable';
        }
    }

    mapExposedAPIStatusToResourceStatus(status) {
        if (status.implementation && status.implementation.ready) {
            return 'available';
        }
        return 'standby';
    }    mapExposedAPIStatusToOperationalState(status) {
        if (status.implementation && status.implementation.ready) {
            return 'enable';
        }
        return 'disable';
    }    /**
     * List all resources (Components and ExposedAPIs) as TMF639 Resources
     */
    async listResources(namespace = null) {
        try {
			namespace = namespace || config.KUBERNETES_NAMESPACE;
			logger.info(`Fetching Resources from namespace: ${namespace}`);
			
            logger.info('KubernetesResourceService: Starting listResources...');
            
            // Get Components from ODA Canvas
            const components = await this.getComponents(namespace);
            logger.info(`KubernetesResourceService: Found ${components.length} Components`);

            // Get ExposedAPIs from ODA Canvas  
            const exposedApis = await this.getExposedAPIs(namespace);
            logger.info(`KubernetesResourceService: Found ${exposedApis.length} ExposedAPIs`);

            // Project DataResourceConfigs into DataResource resources (ADR-011)
            const dataResourceConfigs = await this.getDataResourceConfigs(namespace);
            const { resources: dataResources, componentRelationships } = this.projectDataResources(dataResourceConfigs);

            // Convert Components to TMF639 Resources with related ExposedAPIs and data resources
            const componentResources = components.map(component => {
                const relatedExposedAPIs = this.findRelatedExposedAPIs(component.metadata.name, exposedApis);
                return this.convertComponentToResource(component, relatedExposedAPIs,
                    componentRelationships[component.metadata.name] || []);
            });
            
            // Convert ExposedAPIs to TMF639 Resources with related Component
            const exposedApiResources = exposedApis.map(api => {
                const relatedComponent = this.findRelatedComponent(api, components);
                return this.convertExposedAPIToResource(api, relatedComponent);
            });

            // Combine all resources
            const allResources = [...componentResources, ...exposedApiResources, ...dataResources];
            logger.info(`KubernetesResourceService: Returning ${allResources.length} total resources`);
            
            return allResources;
        } catch (error) {
            logger.error('KubernetesResourceService: Error listing resources:', error.message);
            if (error.response) {
                logger.error('KubernetesResourceService: Response status:', error.response.statusCode);
                logger.error('KubernetesResourceService: Response body:', JSON.stringify(error.response.body, null, 2));
            }
            throw error;
        }
    }    /**
     * Get a specific resource by ID
     */
    async getResourceById(id, namespace = config.KUBERNETES_NAMESPACE) {
        try {
            logger.info(`KubernetesResourceService: Getting resource by ID: ${id}`);
            
            // Try to find as Component first
            const component = await this.getComponent(id, namespace);
            if (component) {
                logger.info(`KubernetesResourceService: Found Component: ${id}`);
                // Get related ExposedAPIs for this Component
                const exposedApis = await this.getExposedAPIs(namespace);
                const relatedExposedAPIs = this.findRelatedExposedAPIs(component.metadata.name, exposedApis);
                const { componentRelationships } = this.projectDataResources(await this.getDataResourceConfigs(namespace));
                return this.convertComponentToResource(component, relatedExposedAPIs,
                    componentRelationships[component.metadata.name] || []);
            }

            // A DataResource id is <dataresourceconfig>.<item> or <dataresourceconfig>.<kind>.<id> (ADR-011)
            if (id.includes('.')) {
                const { resources } = this.projectDataResources(await this.getDataResourceConfigs(namespace));
                const dataResource = resources.find(r => r.id === id);
                if (dataResource) {
                    return dataResource;
                }
            }

            // Try to find as ExposedAPI
            const exposedApi = await this.getExposedAPI(id, namespace);
            if (exposedApi) {
                logger.info(`KubernetesResourceService: Found ExposedAPI: ${id}`);
                // Get related Component for this ExposedAPI
                const components = await this.getComponents(namespace);
                const relatedComponent = this.findRelatedComponent(exposedApi, components);
                return this.convertExposedAPIToResource(exposedApi, relatedComponent);
            }

            logger.info(`KubernetesResourceService: Resource not found: ${id}`);
            return null;
        } catch (error) {
            logger.error(`KubernetesResourceService: Error getting resource ${id}:`, error.message);
            throw error;
        }
    }
}

module.exports = KubernetesResourceService;