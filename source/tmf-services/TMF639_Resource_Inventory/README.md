# TMF639 Resource Inventory Management API

This is a TMF639 Resource Inventory Management API implementation for the ODA Canvas that provides read-only access to ODA Canvas resources including Components and ExposedAPIs.

## Overview

The TMF639 Resource Inventory service wraps the Kubernetes API to provide TMF-compliant access to ODA Canvas resources. It offers read-only operations to query Components and ExposedAPIs deployed in the ODA Canvas Kubernetes cluster.

## Features

- **Read-only Access**: Only supports list and retrieve operations for security
- **Component Resources**: Access to ODA Components with their metadata and status
- **ExposedAPI Resources**: Access to exposed APIs with their configuration details
- **TMF639 Compliance**: Full compliance with TMF639 Resource Inventory Management specification
- **Kubernetes Integration**: Direct integration with ODA Canvas Kubernetes custom resources

## API Endpoints

### Resources
- `GET /tmf-api/resourceInventoryManagement/v5/resource` - List all resources (Components and ExposedAPIs)
- `GET /tmf-api/resourceInventoryManagement/v5/resource/{id}` - Retrieve a specific resource

### Hub Management
- `POST /tmf-api/resourceInventoryManagement/v5/hub` - Register for notifications
- `DELETE /tmf-api/resourceInventoryManagement/v5/hub/{id}` - Unregister from notifications

### Notification Listeners
- `POST /listener/resourceCreateEvent` - Resource creation notifications
- `POST /listener/resourceStateChangeEvent` - Resource state change notifications
- `POST /listener/resourceDeleteEvent` - Resource deletion notifications

## Resource Mapping

### ODA Components → TMF639 Resources
- Component metadata mapped to resource characteristics
- Component status mapped to resource operational state
- Component namespace mapped to resource place

### ExposedAPIs → TMF639 Resources  
- Characteristics `namespace`, `apiName`, `apiType`, `url` (the gateway URL from `status.apiStatus.url`),
  `implementation` (the Service name), `port` and `path` (`spec.port` and `spec.path`, from 1.2.1), `specification`
  and `apiDocs`. In-cluster clients such as the Canvas Portal build
  `http://<implementation>.<namespace>.svc.cluster.local:<port><path>` from them, because the gateway URL may not
  resolve inside the cluster
- API specification mapped to resource characteristics
- API status mapped to resource operational state
- API configuration mapped to resource properties

### DataResourceConfigs → TMF639 `DataResource` resources (ADR-011)
DataResourceConfigs (`oda.tmforum.org/v1alpha1`, ADR-010) in the namespace are projected at two levels, as category
`DataResource` with `resourceSpecification.id` `DataResource`. Without the CRD the category is simply empty.

| Level | Source | Id | Characteristics | Relationships |
|---|---|---|---|---|
| Store | `spec.providedDataResources[]` of a provider (`provider: odaComponent`, `role: provider`) | `<drc>.<item>` | `type`, `engine`, `namespace`, `dataResourceConfig`, `description` | `providedBy` → ODAComponent, `contains` → its content entries |
| Store | `spec.dependentDataResources[]` of a Databricks DataResourceConfig | `<drc>.<item>` | `type` (the item's, else vector-search → vectorStore, otherwise dataProduct), `engine: databricks`, `namespace`, `dataResourceConfig`, `dataProductType`, `accessType` | none |
| Content | `status.providedResources[]` (knowledge-bases and ontology modules mirrored by TMFOP012) | `<drc>.knowledge.<id>`, `<drc>.knowledgeSpecification.<id>` | `kind`, `storeKind` (knowledge-bases), `layer` (modules), `lifecycleStatus`, `namespace`, `href` | `providedBy` → ODAComponent, `isPartOf` → the stores that hold it (a hybrid knowledge-base both, a module the knowledge graph) |

ODAComponent resources gain the characteristic `componentId` (`componentMetadata.id`, for example `TMFC060`; the
`id` characteristic stays), `provides` → every DataResource they serve, and `dependsOn` → the knowledge-base a
consumer DataResourceConfig binds them to (`status.bindings[]`; the provider's store while no knowledge-base is
bound) and the Databricks stores they declare.

Status: store entries are `available`/`enable` when the DataResourceConfig's `Ready` condition is True (Databricks:
`phase: Ready`), else `standby`/`disable`. Content entries follow `lifecycleStatus`: published → `available`/`enable`,
deprecated → `suspended`, rejected → `unknown`, the others (draft, proposed, inReview, agreed) → `standby`/`disable`.

Query them with `GET /resource?category=DataResource`, or get one by its id.

## Configuration

### Environment Variables
- `KUBERNETES_NAMESPACE`: Target namespace (default: 'canvas')
- `KUBERNETES_SERVICE_HOST`: Automatically set in cluster
- `SERVER_URL`: Override server URL
- `NODE_ENV`: Environment mode

### Database Configuration
- MongoDB connection for event subscriptions and notifications
- Database name: `resinv`
- Default host: `canvas-resinv-mongodb`

## Running the Service

### Development
```bash
npm install
npm start
```

### Docker
```bash
docker build -t tmf639-resource-inventory .
docker run -p 8639:8639 tmf639-resource-inventory
```

### Kubernetes Deployment
The service requires appropriate RBAC permissions to access ODA Canvas custom resources:
- Components (components.oda.tmforum.org)
- ExposedAPIs (exposedapis.oda.tmforum.org)

## API Documentation

Once running, the API documentation is available at:
- Swagger UI: `http://localhost:8639/tmf-api/resourceInventoryManagement/v5/api-docs`
- OpenAPI Spec: `http://localhost:8639/tmf-api/resourceInventoryManagement/v5/openapi`

## Security

- Read-only access ensures no modifications to ODA Canvas resources
- Kubernetes RBAC controls access to underlying resources
- TMF639 standard compliance for industry interoperability

## Dependencies

- Node.js >= 14
- Kubernetes cluster with ODA Canvas installed
- MongoDB for event management
- Appropriate Kubernetes RBAC permissions

## License

Licensed under the same terms as the ODA Canvas project.
