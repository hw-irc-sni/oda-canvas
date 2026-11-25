/* eslint-disable no-unused-vars */
const Service = require('./Service');
const KubernetesResourceService = require('./KubernetesResourceService');
const logger = require('../logger');

// Initialize Kubernetes service
const k8sService = new KubernetesResourceService();

/**
 * List or find Resource objects from ODA Canvas Kubernetes cluster
 * This operation lists Resources from Components and ExposedAPIs
 *
 * fields String Comma-separated properties to be provided in response (optional)
 * offset Integer Requested index for start of resources to be provided in response (optional)
 * limit Integer Requested number of resources to be provided in response (optional)
 * returns List
 **/
const RESERVED_QUERY_PARAMS = ['namespace', 'filter', 'fields', 'offset', 'limit', 'sort'];

/**
 * Keeps the resources whose top-level attribute equals one of the comma-separated values of each query
 * parameter (for example category=DataResource). Parameters naming no scalar attribute are ignored.
 */
function applyAttributeFilters(resources, params) {
  const filters = Object.entries(params).filter(([name, value]) =>
    !RESERVED_QUERY_PARAMS.includes(name) && typeof value === 'string' && value !== '');
  return filters.reduce((items, [name, value]) => {
    const wanted = value.split(',').map(v => v.trim());
    return items.filter(resource => {
      const actual = resource[name];
      if (actual === undefined || (typeof actual === 'object' && actual !== null)) return true;
      return wanted.includes(String(actual));
    });
  }, resources);
}

const listResource = (args, context /* fieldsoffsetlimit */) =>
  new Promise(
    async (resolve) => {
      context.classname = "Resource";
      context.operationId = "listResource";
      context.method = "get";
      try {
		let namespace = args.namespace || (args.dynamic && args.dynamic.namespace) || null;
		logger.info(`namespace: ${namespace}`);

        // Get all resources from Kubernetes using the new method
        let allResources = await k8sService.listResources(namespace);

        // Apply JSONPath filtering if filter param is present
        let filterQuery = args.filter || (args.dynamic && args.dynamic.filter);
        
        if (filterQuery) {
          const { applyJSONPath } = require('../utils/jsonpath');
          
          logger.info(`Applying JSONPath filter: ${filterQuery}`);
          
          try {
            const filtered = await applyJSONPath(allResources, filterQuery);
            logger.info(`Filtered resources count: ${filtered.length}`);
            allResources = filtered;
          } catch (err) {
            logger.error("listResource: JSONPath filter error=" + err);
            resolve(Service.rejectResponse(
              err.message || 'Invalid filter',
              err.status || 400,
            ));
            return;
          }
        }

        // TMF630 attribute filters (?category=DataResource&resourceStatus=available): equality on top-level
        // attributes, a comma separating alternatives
        allResources = applyAttributeFilters(allResources, args.dynamic || {});

        // Apply pagination, fields, etc.
        const filteredResources = Service.applyQuery(allResources, args, context);
        resolve(Service.createResponse(filteredResources, context));

      } catch (e) {
        logger.error("listResource: error=" + e);
        resolve(Service.rejectResponse(
          e.message || 'Invalid input',
          e.status || 500,
        ));
      }
    }
  );
/**
 * Retrieves a Resource by ID from ODA Canvas Kubernetes cluster
 * This operation retrieves a Resource entity. Supports Components and ExposedAPIs.
 *
 * id String Identifier of the Resource
 * fields String Comma-separated properties to be provided in response (optional)
 * returns Resource
 **/
const retrieveResource = (args, context /* idfields */) =>
  new Promise(
    async (resolve) => {
      context.classname = "Resource";
      context.operationId = "retrieveResource";
      context.method = "get";      
      try {

		let namespace = args.namespace || (args.dynamic && args.dynamic.namespace);
		logger.info(`namespace: ${namespace}`);
		
        // Get the specific resource by ID
        const resource = await k8sService.getResourceById(args.id, namespace);

        if (!resource) {
          resolve(Service.rejectResponse(
            `Resource with id ${args.id} not found`,
            404,
          ));
          return;
        }

        // Apply field filtering if specified
        const filteredResource = Service.applyFields(resource, args.fields);
        
        resolve(Service.createResponse(filteredResource, context));

      } catch (e) {
        logger.error("retrieveResource: error=" + e);
        resolve(Service.rejectResponse(
          e.message || 'Invalid input',
          e.status || 500,
        ));
      }
    },
  );

module.exports = {
  applyAttributeFilters,
  listResource,
  retrieveResource,
};
