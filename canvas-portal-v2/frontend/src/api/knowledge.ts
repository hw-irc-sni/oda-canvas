import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import axios, { type AxiosProgressEvent } from 'axios'
import apiClient from './client'

// The AI > Knowledge page's API: the TMFC060 Knowledge Management instances and, through the portal backend's
// proxy, their TMFxxx Knowledge Management v5 and TMF701 APIs. The types follow the OAS files of
// source/components/TMFC060-KnowledgeManagement/knowledgeApiMicroservice/api. Response fields are optional
// unless the OAS requires them.

// ---- instances -----------------------------------------------------------------------------------------------

export interface LifecycleTransition {
  from: LifecycleStatus
  to: LifecycleStatus
}

/** What the user's roles allow on an instance. The component checks every call again. */
export interface Capabilities {
  roles: string[]
  /** operationId -> allowed */
  operations: Record<string, boolean>
  transitions: { knowledge?: LifecycleTransition[]; knowledgeSpecification?: LifecycleTransition[] }
  reviewKinds: string[]
}

export interface KnowledgeInstance {
  /** The component name, which is also the instance's Keycloak client id. */
  name: string
  label: string
  namespace: string | null
  version: string | null
  resourceStatus: string | null
  deploymentStatus: string | null
  available: boolean
  apiUrl: string | null
  processUrl: string | null
  mcpUrl: string | null
  source: 'ri' | 'static'
  /** Null when the instance is unavailable or its API description can't be read (see capabilitiesError). */
  capabilities: Capabilities | null
  capabilitiesError: string | null
}

export interface KnowledgeInstanceList {
  discovery: 'ri' | 'static'
  /** True when Resource Inventory is unreachable and the last good list is shown. */
  stale: boolean
  instances: KnowledgeInstance[]
}

// ---- common --------------------------------------------------------------------------------------------------

export type LifecycleStatus =
  | 'draft'
  | 'proposed'
  | 'inReview'
  | 'agreed'
  | 'rejected'
  | 'published'
  | 'deprecated'

export type LinkStatus = 'provisional' | 'confirmed'

export interface EntityRef {
  id: string
  href?: string
  name?: string
  '@type'?: string
  '@referredType'?: string
}

export interface RelatedParty {
  '@type'?: string
  role?: string
  partyOrPartyRole?: EntityRef & { partyId?: string; partyName?: string }
}

export interface TimePeriod {
  startDateTime?: string
  endDateTime?: string
}

/** A reference to an ontology module, by id or by ontology IRI. */
export interface KnowledgeSpecificationRef {
  '@type'?: string
  id?: string
  iri?: string
  href?: string
  name?: string
}

export type JsonLdDocument = Record<string, unknown> | Record<string, unknown>[]

// ---- knowledge-bases -----------------------------------------------------------------------------------------

export type KnowledgeCategory = 'know-what' | 'know-why' | 'know-how' | 'know-who'
export type StoreKind = 'graph' | 'vector' | 'hybrid'
export type KnowledgeSourceKind = 'upload' | 'tmf639' | 'tmf638' | 'tmf642' | 'tmf628' | 'tmf921' | 'remote'

export interface KnowledgeSource {
  kind: KnowledgeSourceKind
  dependentApiName?: string
  eventTypes?: string[]
  query?: string
  url?: string
}

export interface RetrievalSettings {
  topK?: number
  scoreThreshold?: number
  hybrid?: boolean
}

export interface KnowledgeStatistics {
  triples?: number
  documents?: number
  chunks?: number
}

export interface Knowledge {
  '@type'?: string
  id: string
  href?: string
  name?: string
  description?: string
  version?: string
  lifecycleStatus?: LifecycleStatus
  lastUpdate?: string
  knowledgeCategory?: KnowledgeCategory[]
  storeKind?: StoreKind
  conformsTo?: KnowledgeSpecificationRef[]
  source?: KnowledgeSource[]
  retrieval?: RetrievalSettings
  statistics?: KnowledgeStatistics
  relatedParty?: RelatedParty[]
  validFor?: TimePeriod
}

export interface KnowledgeInput {
  name: string
  description?: string
  version?: string
  knowledgeCategory?: KnowledgeCategory[]
  /** Can't change once the knowledge-base exists. */
  storeKind?: StoreKind
  conformsTo?: KnowledgeSpecificationRef[]
  source?: KnowledgeSource[]
  retrieval?: RetrievalSettings
  relatedParty?: RelatedParty[]
  validFor?: TimePeriod
}

/** A lifecycleStatus change must be on its own. The component accepts no null (a field can't be removed). */
export type KnowledgePatch = Partial<Omit<KnowledgeInput, 'storeKind'>> | { lifecycleStatus: LifecycleStatus }

export type RdfMediaType = 'text/turtle' | 'application/ld+json'

export interface SearchRequest {
  query: string
  topK?: number
  scoreThreshold?: number
  hybrid?: boolean
  /** Keeps only chunks that mention one of these ontology IRIs or a subclass of one. */
  iriFilter?: string[]
}

export interface SearchHit {
  chunkId: string
  text: string
  score: number
  documentId?: string
  entityIri?: string[]
}

export interface SearchResult {
  hits: SearchHit[]
  /** 'sparse' when a hybrid search ran without embeddings. Undefined when the backend doesn't pass the header. */
  mode?: string
}

export interface RecommendationRequest {
  context?: string
  iri?: string[]
  topK?: number
}

export interface RecommendationItem {
  itemId: string
  kind: 'entity' | 'document' | 'chunk'
  iri?: string
  label?: string
  score: number
  signals?: { successRate?: number; applicability?: number; usage?: number }
}

export interface RecommendationResult {
  recommendationId?: string
  items?: RecommendationItem[]
}

export interface RecommendationFeedback {
  recommendationId: string
  itemId: string
  used?: boolean
  outcome?: 'success' | 'failure' | 'ignored'
}

export interface SparqlTerm {
  type: 'uri' | 'literal' | 'bnode' | 'triple'
  value: string
  datatype?: string
  'xml:lang'?: string
}

export interface SparqlResults {
  head?: { vars?: string[]; link?: string[] }
  results?: { bindings?: Record<string, SparqlTerm>[] }
  boolean?: boolean
}

/** A SELECT or ASK answer, or the Turtle of a CONSTRUCT or DESCRIBE. */
export type QueryAnswer = ({ kind: 'results'; results: SparqlResults } | { kind: 'graph'; turtle: string }) & {
  /** True when the server's row limit cut the result. */
  truncated: boolean
}

export interface KnowledgeDocument {
  id: string
  href?: string
  name?: string
  description?: string
  mediaType?: string
  size?: number
  sha256?: string
  uploadDate?: string
  ingestionState?: 'pending' | 'ingested' | 'failed'
  lastIngestion?: EntityRef
}

export interface DocumentUpload {
  file: File
  name?: string
  description?: string
  onProgress?: (percent: number) => void
}

export interface IngestRequest {
  source?: KnowledgeSourceKind
  documentId?: string[]
  params?: Record<string, unknown>
}

/** A change the ingestion pipeline proposes instead of applying it. */
export interface KnowledgeProposal {
  '@type'?: string
  id: string
  href?: string
  kind?: 'dataConflict' | 'ontologyTerm' | 'entityLink'
  state?: 'proposed' | 'accepted' | 'rejected'
  summary?: string
  source?: string
  processId?: string
  details?: Record<string, unknown>
  suggestion?: Record<string, unknown>
  decidedBy?: string
  decidedAt?: string
  creationDate?: string
}

export interface KnowledgeProposalDecision {
  state: 'accepted' | 'rejected'
  choice?: string
}

// ---- ontology modules ----------------------------------------------------------------------------------------

export type OntologyLayer = 'Foundational' | 'Telecom' | 'Implementation'
export type SpecKind = 'ontology' | 'vocabulary' | 'shapes' | 'bridge' | 'remote'

export type KnowledgeSpecificationSource =
  | { kind: 'upload' }
  | { kind: 'git'; url: string; ref?: string; path: string; secretRef?: string }
  | { kind: 'mount'; path: string }
  | { kind: 'componentContribution'; componentId?: string; componentName?: string }
  | { kind: 'remoteEndpoint'; url: string; endpointKind?: 'sparql' | 'tmfc060' }

export interface KnowledgeSpecification {
  /** FederatedKnowledgeSpecification: governed by its federation repository, so read-only here. */
  '@type'?: string
  id: string
  href?: string
  name?: string
  description?: string
  version?: string
  iri?: string
  prefix?: string
  layer?: OntologyLayer
  specKind?: SpecKind
  depth?: string
  owner?: RelatedParty
  imports?: KnowledgeSpecificationRef[]
  describes?: KnowledgeSpecificationRef[]
  source?: KnowledgeSpecificationSource
  lifecycleStatus?: LifecycleStatus
  linkStatus?: LinkStatus
  lastUpdate?: string
  /** Of a federated module: its status in the source repository. */
  sourceStatus?: string
}

export interface KnowledgeSpecificationInput {
  name: string
  description?: string
  version?: string
  /** Can't change once the module is registered. */
  iri: string
  prefix?: string
  layer?: OntologyLayer
  specKind: SpecKind
  depth?: string
  owner?: RelatedParty
  imports?: KnowledgeSpecificationRef[]
  describes?: KnowledgeSpecificationRef[]
  source: KnowledgeSpecificationSource
}

export type KnowledgeSpecificationPatch =
  | Partial<Omit<KnowledgeSpecificationInput, 'iri'>>
  | { lifecycleStatus: LifecycleStatus }

export type MappingConfidence = 'high' | 'medium' | 'low'

export interface Mapping {
  id: string
  href?: string
  subject?: string
  predicate?: string
  object?: string
  confidence?: MappingConfidence
  rationale?: string
  lifecycleStatus?: LifecycleStatus
  /** FTO's bridge:status, derived from lifecycleStatus. */
  status?: 'agreed' | 'proposed'
}

export interface MappingInput {
  subject: string
  predicate: string
  object: string
  confidence?: MappingConfidence
  rationale?: string
}

export type MappingPatch = Partial<MappingInput> | { lifecycleStatus: LifecycleStatus }

export interface ReviewTarget {
  kind: 'module' | 'mapping'
  id: string
}

export interface ReviewItem {
  id: string
  kind?: 'proposal' | 'comment' | 'ruling'
  target?: ReviewTarget
  text?: string
  ruling?: 'agreed' | 'rejected'
  linkStatus?: LinkStatus
  author?: string
  approver?: string
  date?: string
}

export interface ReviewItemInput {
  kind: 'proposal' | 'comment' | 'ruling'
  target: ReviewTarget
  text?: string
  ruling?: 'agreed' | 'rejected'
  linkStatus?: LinkStatus
}

export type ValidationRule =
  | 'parse'
  | 'layerAnnotation'
  | 'layerMonotonicity'
  | 'parentsResolve'
  | 'catalogIntegrity'

export interface ShaclResult {
  focusNode?: string
  resultPath?: string
  severity?: 'Violation' | 'Warning' | 'Info'
  message?: string
  sourceShape?: string
}

export interface ValidationReport {
  moduleId?: string
  runAt?: string
  conforms?: boolean
  rules?: { rule: ValidationRule; passed: boolean; messages?: { severity: 'error' | 'warning'; text: string }[] }[]
  shacl?: { conforms?: boolean; results?: ShaclResult[] }
}

export interface DocumentValidationReport {
  shapesModule?: string
  runAt?: string
  conforms?: boolean
  results?: ShaclResult[]
}

export interface TermDescription {
  iri?: string
  curie?: string
  definedIn?: string[]
  labels?: string[]
  definitions?: string[]
  types?: string[]
  parents?: string[]
  matches?: { predicate?: string; object?: string }[]
  layer?: OntologyLayer
  linkStatus?: LinkStatus
}

export interface TermHit {
  iri?: string
  curie?: string
  module?: string
  layer?: OntologyLayer
  via?: string
  text?: string
}

export interface LayerChainEntry {
  term?: string
  layer?: OntologyLayer
  module?: string
  linkStatus?: LinkStatus
}

export interface TermLookupResult {
  description?: TermDescription
  hits?: TermHit[]
  layerChain?: { chain?: LayerChainEntry[]; endsAtFoundational?: boolean }
}

export interface TermSearch {
  q: string
  limit?: number
  includeUnpublished?: boolean
}

export interface TermDescribe {
  /** An IRI, or a CURIE of a registered prefix. */
  iri: string
  layerChain?: boolean
  includeUnpublished?: boolean
}

// ---- processes (TMF701) --------------------------------------------------------------------------------------

export type ProcessState = 'acknowledged' | 'inProgress' | 'completed' | 'failed'

export interface Process {
  '@type'?: string
  id: string
  href?: string
  state?: ProcessState
  description?: string
  creationDate?: string
  completionDate?: string
  errorMessage?: string
  knowledge?: { id?: string; href?: string }
  task?: { '@type'?: string; id?: string; href?: string }[]
}

export interface ProcessTask {
  '@type'?: string
  id: string
  href?: string
  processId?: string
  process?: { id?: string; href?: string }
  step?: 'KM-FX-01' | 'KM-FX-02' | 'KM-FX-03' | 'KM-FX-04' | 'KM-FX-05' | 'KM-FX-06'
  documentId?: string
  state?: ProcessState
  attempt?: number
  errorMessage?: string
  creationDate?: string
  completionDate?: string
}

// ---- errors --------------------------------------------------------------------------------------------------

function parsed(data: unknown): unknown {
  if (typeof data !== 'string') return data
  try {
    return JSON.parse(data)
  } catch {
    return undefined
  }
}

/**
 * The message to show for a failed call: the component's TMF630/RFC 7807 error (message, detail, reason) or the
 * portal backend's {detail}. Both are safe to show.
 */
export function knowledgeErrorMessage(error: unknown, fallback = 'The request to the knowledge instance failed.'): string {
  if (!axios.isAxiosError(error)) return error instanceof Error && error.message ? error.message : fallback
  if (!error.response) return 'The portal backend is unreachable.'
  // Calls that ask for text (Turtle, SPARQL) get their errors as text too.
  const body = parsed(error.response.data)
  if (body && typeof body === 'object') {
    const { message, detail, reason } = body as { message?: unknown; detail?: unknown; reason?: unknown }
    for (const text of [message, detail, reason]) {
      if (typeof text === 'string' && text.trim()) return text
    }
    if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg
  }
  if (error.response.status === 413) return 'The request is larger than the portal allows.'
  return `${fallback} (HTTP ${error.response.status})`
}

export function knowledgeErrorStatus(error: unknown): number | undefined {
  return axios.isAxiosError(error) ? error.response?.status : undefined
}

// ---- calls ---------------------------------------------------------------------------------------------------

/** The proxy URL of an instance's API path. Every segment is encoded, so ids and CURIEs can't change the path. */
function url(instance: string, ...segments: string[]): string {
  return ['/api/knowledge', ...[instance, ...segments].map(encodeURIComponent)].join('/')
}

const MERGE_PATCH = { headers: { 'Content-Type': 'application/merge-patch+json' } }

async function getJson<T>(path: string, params?: object): Promise<T> {
  const { data } = await apiClient.get<T>(path, { params })
  return data
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const { data } = await apiClient.post<T>(path, body)
  return data
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const { data } = await apiClient.patch<T>(path, body, MERGE_PATCH)
  return data
}

async function getText(path: string, accept: string): Promise<string> {
  // responseType 'text' stops axios from parsing a JSON-LD body.
  const { data } = await apiClient.get<string>(path, { responseType: 'text', headers: { Accept: accept } })
  return data
}

export function fetchKnowledgeInstances(): Promise<KnowledgeInstanceList> {
  return getJson('/api/knowledge/instances')
}

// Knowledge-bases
/** listKnowledge */
export function fetchKnowledgeBases(instance: string): Promise<Knowledge[]> {
  return getJson(url(instance, 'knowledge'))
}

/** createKnowledge */
export function createKnowledgeBase(instance: string, input: KnowledgeInput): Promise<Knowledge> {
  return postJson(url(instance, 'knowledge'), input)
}

/** retrieveKnowledge */
export function fetchKnowledgeBase(instance: string, id: string): Promise<Knowledge> {
  return getJson(url(instance, 'knowledge', id))
}

/** patchKnowledge */
export function patchKnowledgeBase(instance: string, { id, patch }: { id: string; patch: KnowledgePatch }): Promise<Knowledge> {
  return patchJson(url(instance, 'knowledge', id), patch)
}

/** deleteKnowledge */
export async function deleteKnowledgeBase(instance: string, id: string): Promise<void> {
  await apiClient.delete(url(instance, 'knowledge', id))
}

/** retrieveKnowledgeGraph */
export function fetchKnowledgeGraph(instance: string, id: string, mediaType: RdfMediaType = 'text/turtle'): Promise<string> {
  return getText(url(instance, 'knowledge', id, 'graph'), mediaType)
}

export interface GraphWrite {
  id: string
  content: string
  mediaType: RdfMediaType
}

/** replaceKnowledgeGraph */
export async function replaceKnowledgeGraph(instance: string, { id, content, mediaType }: GraphWrite): Promise<void> {
  await apiClient.put(url(instance, 'knowledge', id, 'graph'), content, { headers: { 'Content-Type': mediaType } })
}

/** mergeKnowledgeGraph */
export async function mergeKnowledgeGraph(instance: string, { id, content, mediaType }: GraphWrite): Promise<void> {
  await apiClient.post(url(instance, 'knowledge', id, 'graph'), content, { headers: { 'Content-Type': mediaType } })
}

/** clearKnowledgeGraph */
export async function clearKnowledgeGraph(instance: string, id: string): Promise<void> {
  await apiClient.delete(url(instance, 'knowledge', id, 'graph'))
}

/** queryKnowledge: a read-only SPARQL query; timeout in seconds, capped by the server. */
export async function queryKnowledge(
  instance: string,
  { id, query, timeout }: { id: string; query: string; timeout?: number },
): Promise<QueryAnswer> {
  const { data, headers } = await apiClient.post<string>(url(instance, 'knowledge', id, 'query'), query, {
    params: { timeout },
    responseType: 'text',
    headers: {
      'Content-Type': 'application/sparql-query',
      Accept: 'application/sparql-results+json, text/turtle;q=0.9',
    },
  })
  const truncated = headers['x-result-truncated'] === 'true'
  if (String(headers['content-type'] ?? '').startsWith('text/turtle')) return { kind: 'graph', turtle: data, truncated }
  return { kind: 'results', results: JSON.parse(data) as SparqlResults, truncated }
}

/** searchKnowledge */
export async function searchKnowledge(
  instance: string,
  { id, request }: { id: string; request: SearchRequest },
): Promise<SearchResult> {
  const { data, headers } = await apiClient.post<{ hits?: SearchHit[] }>(url(instance, 'knowledge', id, 'search'), request)
  const mode = headers['x-search-mode']
  return { hits: data.hits ?? [], mode: typeof mode === 'string' ? mode : undefined }
}

/** recommendFromKnowledge */
export function recommendFromKnowledge(
  instance: string,
  { id, request }: { id: string; request: RecommendationRequest },
): Promise<RecommendationResult> {
  return postJson(url(instance, 'knowledge', id, 'recommend'), request)
}

/** createRecommendationFeedback */
export async function sendRecommendationFeedback(
  instance: string,
  { id, feedback }: { id: string; feedback: RecommendationFeedback },
): Promise<void> {
  await apiClient.post(url(instance, 'knowledge', id, 'recommend', 'feedback'), feedback)
}

/** listKnowledgeDocument */
export function fetchDocuments(instance: string, id: string): Promise<KnowledgeDocument[]> {
  return getJson(url(instance, 'knowledge', id, 'document'))
}

/** createKnowledgeDocument */
export async function uploadDocument(
  instance: string,
  { id, upload }: { id: string; upload: DocumentUpload },
): Promise<KnowledgeDocument> {
  const form = new FormData()
  if (upload.name) form.append('name', upload.name)
  if (upload.description) form.append('description', upload.description)
  form.append('file', upload.file, upload.file.name)
  // Without this, the client's default application/json makes axios send the form as JSON.
  const { data } = await apiClient.post<KnowledgeDocument>(url(instance, 'knowledge', id, 'document'), form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (event: AxiosProgressEvent) => {
      if (upload.onProgress && event.total) upload.onProgress(Math.round((event.loaded / event.total) * 100))
    },
  })
  return data
}

/** retrieveKnowledgeDocument */
export function fetchDocument(instance: string, id: string, documentId: string): Promise<KnowledgeDocument> {
  return getJson(url(instance, 'knowledge', id, 'document', documentId))
}

/** deleteKnowledgeDocument */
export async function deleteDocument(instance: string, { id, documentId }: { id: string; documentId: string }): Promise<void> {
  await apiClient.delete(url(instance, 'knowledge', id, 'document', documentId))
}

/**
 * retrieveKnowledgeDocumentContent. The bytes are whatever an editor uploaded, HTML included, so the blob is typed
 * application/octet-stream: it is for saving (see saveBlob), never for showing in the portal's origin.
 */
export async function fetchDocumentContent(instance: string, id: string, documentId: string): Promise<Blob> {
  try {
    const { data } = await apiClient.get<ArrayBuffer>(url(instance, 'knowledge', id, 'document', documentId, 'content'), {
      responseType: 'arraybuffer',
      headers: { Accept: '*/*' },
    })
    return new Blob([data], { type: 'application/octet-stream' })
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.data instanceof ArrayBuffer) {
      error.response.data = new TextDecoder().decode(error.response.data)
    }
    throw error
  }
}

/** createKnowledgeIngestion: starts a TMF701 process and returns its reference. */
export function startIngestion(instance: string, { id, request }: { id: string; request: IngestRequest }): Promise<EntityRef> {
  return postJson(url(instance, 'knowledge', id, 'ingest'), request)
}

/** listKnowledgeProposal */
export function fetchProposals(instance: string, id: string): Promise<KnowledgeProposal[]> {
  return getJson(url(instance, 'knowledge', id, 'proposal'))
}

/** retrieveKnowledgeProposal */
export function fetchProposal(instance: string, id: string, proposalId: string): Promise<KnowledgeProposal> {
  return getJson(url(instance, 'knowledge', id, 'proposal', proposalId))
}

/** patchKnowledgeProposal */
export async function decideProposal(
  instance: string,
  { id, proposalId, decision }: { id: string; proposalId: string; decision: KnowledgeProposalDecision },
): Promise<KnowledgeProposal> {
  const { data } = await apiClient.patch<KnowledgeProposal>(url(instance, 'knowledge', id, 'proposal', proposalId), decision)
  return data
}

// Ontology modules
/** listKnowledgeSpecification */
export function fetchModules(instance: string): Promise<KnowledgeSpecification[]> {
  return getJson(url(instance, 'knowledgeSpecification'))
}

/** createKnowledgeSpecification */
export function createModule(instance: string, input: KnowledgeSpecificationInput): Promise<KnowledgeSpecification> {
  return postJson(url(instance, 'knowledgeSpecification'), input)
}

/** retrieveKnowledgeSpecification */
export function fetchModule(instance: string, id: string): Promise<KnowledgeSpecification> {
  return getJson(url(instance, 'knowledgeSpecification', id))
}

/** patchKnowledgeSpecification */
export function patchModule(
  instance: string,
  { id, patch }: { id: string; patch: KnowledgeSpecificationPatch },
): Promise<KnowledgeSpecification> {
  return patchJson(url(instance, 'knowledgeSpecification', id), patch)
}

/** deleteKnowledgeSpecification */
export async function deleteModule(instance: string, id: string): Promise<void> {
  await apiClient.delete(url(instance, 'knowledgeSpecification', id))
}

/** retrieveKnowledgeSpecificationContent */
export function fetchModuleContent(instance: string, id: string): Promise<string> {
  return getText(url(instance, 'knowledgeSpecification', id, 'content'), 'text/turtle')
}

/** replaceKnowledgeSpecificationContent */
export async function replaceModuleContent(instance: string, { id, turtle }: { id: string; turtle: string }): Promise<void> {
  await apiClient.put(url(instance, 'knowledgeSpecification', id, 'content'), turtle, {
    headers: { 'Content-Type': 'text/turtle' },
  })
}

/** listKnowledgeSpecificationMapping */
export function fetchMappings(instance: string, id: string): Promise<Mapping[]> {
  return getJson(url(instance, 'knowledgeSpecification', id, 'mapping'))
}

/** createKnowledgeSpecificationMapping */
export function createMapping(instance: string, { id, input }: { id: string; input: MappingInput }): Promise<Mapping> {
  return postJson(url(instance, 'knowledgeSpecification', id, 'mapping'), input)
}

/** retrieveKnowledgeSpecificationMapping */
export function fetchMapping(instance: string, id: string, mappingId: string): Promise<Mapping> {
  return getJson(url(instance, 'knowledgeSpecification', id, 'mapping', mappingId))
}

/** patchKnowledgeSpecificationMapping */
export function patchMapping(
  instance: string,
  { id, mappingId, patch }: { id: string; mappingId: string; patch: MappingPatch },
): Promise<Mapping> {
  return patchJson(url(instance, 'knowledgeSpecification', id, 'mapping', mappingId), patch)
}

/** deleteKnowledgeSpecificationMapping */
export async function deleteMapping(instance: string, { id, mappingId }: { id: string; mappingId: string }): Promise<void> {
  await apiClient.delete(url(instance, 'knowledgeSpecification', id, 'mapping', mappingId))
}

/** listKnowledgeSpecificationReview: the reviews of the module and of its mappings, oldest first. */
export function fetchReviews(instance: string, id: string): Promise<ReviewItem[]> {
  return getJson(url(instance, 'knowledgeSpecification', id, 'review'))
}

/** createKnowledgeSpecificationReview */
export function createReview(instance: string, { id, input }: { id: string; input: ReviewItemInput }): Promise<ReviewItem> {
  return postJson(url(instance, 'knowledgeSpecification', id, 'review'), input)
}

/** retrieveKnowledgeSpecificationValidation: the last report (404 before the first run). */
export function fetchValidation(instance: string, id: string): Promise<ValidationReport> {
  return getJson(url(instance, 'knowledgeSpecification', id, 'validation'))
}

/** runKnowledgeSpecificationValidation */
export async function runValidation(instance: string, id: string): Promise<ValidationReport> {
  const { data } = await apiClient.post<ValidationReport>(url(instance, 'knowledgeSpecification', id, 'validation'))
  return data
}

/** validateDocument: checks an RDF document against a shapes module; nothing is stored. */
export async function validateDocument(instance: string, { id, content, mediaType }: GraphWrite): Promise<DocumentValidationReport> {
  const { data } = await apiClient.post<DocumentValidationReport>(
    url(instance, 'knowledgeSpecification', id, 'validation', 'document'),
    content,
    { headers: { 'Content-Type': mediaType } },
  )
  return data
}

// Terms
/** retrieveKnowledgeTerm */
export function searchTerms(instance: string, search: TermSearch): Promise<TermLookupResult> {
  return getJson(url(instance, 'knowledgeTerm'), search)
}

/** describeKnowledgeTerm */
export function describeTerm(instance: string, term: TermDescribe): Promise<TermLookupResult> {
  return getJson(url(instance, 'knowledgeTerm', 'describe'), term)
}

// TMF701 processes
/** listProcess */
export function fetchProcesses(instance: string): Promise<Process[]> {
  return getJson(url(instance, 'process'), { sort: '-creationDate' })
}

/** retrieveProcess */
export function fetchProcess(instance: string, id: string): Promise<Process> {
  return getJson(url(instance, 'process', id))
}

/** listTask: the tasks of one process. */
export function fetchTasks(instance: string, processId: string): Promise<ProcessTask[]> {
  return getJson(url(instance, 'task'), { processId })
}

/** retrieveTask */
export function fetchTask(instance: string, id: string): Promise<ProcessTask> {
  return getJson(url(instance, 'task', id))
}

/** Save a file the API returned. The link has a download name, so the browser never renders it. */
export function saveBlob(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(new Blob([blob], { type: 'application/octet-stream' }))
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(href)
}

// ---- hooks ---------------------------------------------------------------------------------------------------

/** What a write can change; each is a query key prefix under the instance. */
type Scope = 'knowledge' | 'specification' | 'term' | 'process'

const INSTANCES_KEY = ['knowledge-instances']

function key(instance: string, scope: Scope, ...rest: unknown[]) {
  return ['knowledge-api', instance, scope, ...rest]
}

/** Retry the portal's 5xx and network errors, never a 4xx: the component has decided. */
function retry(failureCount: number, error: unknown): boolean {
  const status = knowledgeErrorStatus(error)
  return (status === undefined || status >= 500) && failureCount < 2
}

interface QueryOptions<T> {
  enabled?: boolean
  refetchInterval?: number | ((data: T | undefined) => number | false)
}

function useInstanceQuery<T>(queryKey: unknown[], queryFn: () => Promise<T>, options: QueryOptions<T> = {}) {
  const { enabled, refetchInterval } = options
  return useQuery({
    queryKey,
    queryFn,
    retry,
    enabled,
    refetchInterval: typeof refetchInterval === 'function' ? (query) => refetchInterval(query.state.data as T | undefined) : refetchInterval,
  })
}

/**
 * A mutation hook whose queries are refetched when it settles: after a 409 the page shows an outdated state too.
 */
function mutationHook<V, R>(call: (instance: string, variables: V) => Promise<R>, scopes: Scope[]) {
  return function useInstanceMutation(instance: string) {
    const queryClient = useQueryClient()
    return useMutation({
      mutationFn: (variables: V) => call(instance, variables),
      onSettled: () => Promise.all(scopes.map((scope) => queryClient.invalidateQueries({ queryKey: key(instance, scope) }))),
    })
  }
}

const RUNNING: (ProcessState | undefined)[] = ['acknowledged', 'inProgress']
const POLL_MS = 3000

/** True while a process or task is still to finish. */
export function isRunning(state: ProcessState | undefined): boolean {
  return RUNNING.includes(state)
}

export function useKnowledgeInstances() {
  return useQuery({ queryKey: INSTANCES_KEY, queryFn: fetchKnowledgeInstances, retry, refetchInterval: 60_000 })
}

export function useKnowledgeBases(instance: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'knowledge', 'list'), () => fetchKnowledgeBases(instance), options)
}

export function useKnowledgeBase(instance: string, id: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'knowledge', 'item', id), () => fetchKnowledgeBase(instance, id), options)
}

export function useKnowledgeGraph(instance: string, id: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'knowledge', 'item', id, 'graph'), () => fetchKnowledgeGraph(instance, id), options)
}

/** Polls while a document waits for a running ingestion. */
export function useDocuments(instance: string, id: string, options?: { enabled?: boolean; polling?: boolean }) {
  return useInstanceQuery(key(instance, 'knowledge', 'item', id, 'document'), () => fetchDocuments(instance, id), {
    enabled: options?.enabled,
    refetchInterval: options?.polling ? POLL_MS : undefined,
  })
}

export function useDocument(instance: string, id: string, documentId: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(
    key(instance, 'knowledge', 'item', id, 'document', documentId),
    () => fetchDocument(instance, id, documentId),
    options,
  )
}

export function useProposals(instance: string, id: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'knowledge', 'item', id, 'proposal'), () => fetchProposals(instance, id), options)
}

export function useProposal(instance: string, id: string, proposalId: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(
    key(instance, 'knowledge', 'item', id, 'proposal', proposalId),
    () => fetchProposal(instance, id, proposalId),
    options,
  )
}

export function useModules(instance: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'specification', 'list'), () => fetchModules(instance), options)
}

export function useModule(instance: string, id: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'specification', 'item', id), () => fetchModule(instance, id), options)
}

export function useModuleContent(instance: string, id: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'specification', 'item', id, 'content'), () => fetchModuleContent(instance, id), options)
}

export function useMappings(instance: string, id: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'specification', 'item', id, 'mapping'), () => fetchMappings(instance, id), options)
}

export function useMapping(instance: string, id: string, mappingId: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(
    key(instance, 'specification', 'item', id, 'mapping', mappingId),
    () => fetchMapping(instance, id, mappingId),
    options,
  )
}

export function useReviews(instance: string, id: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'specification', 'item', id, 'review'), () => fetchReviews(instance, id), options)
}

/** Null before the first validation run. */
export function useValidation(instance: string, id: string, options?: { enabled?: boolean }) {
  return useInstanceQuery<ValidationReport | null>(
    key(instance, 'specification', 'item', id, 'validation'),
    async () => {
      try {
        return await fetchValidation(instance, id)
      } catch (error) {
        if (knowledgeErrorStatus(error) === 404) return null
        throw error
      }
    },
    options,
  )
}

export function useTermSearch(instance: string, search: TermSearch, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'term', 'search', search), () => searchTerms(instance, search), {
    enabled: (options?.enabled ?? true) && search.q.trim() !== '',
  })
}

export function useTermDescription(instance: string, term: TermDescribe, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'term', 'describe', term), () => describeTerm(instance, term), {
    enabled: (options?.enabled ?? true) && term.iri.trim() !== '',
  })
}

/** Polls while a process is running. */
export function useProcesses(instance: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'process', 'list'), () => fetchProcesses(instance), {
    enabled: options?.enabled,
    refetchInterval: (processes) => (processes?.some((process) => isRunning(process.state)) ? POLL_MS : false),
  })
}

export function useProcess(instance: string, id: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'process', 'item', id), () => fetchProcess(instance, id), {
    enabled: options?.enabled,
    refetchInterval: (process) => (isRunning(process?.state) ? POLL_MS : false),
  })
}

/** Polls while a task is running, or while the caller knows its process is (a step's task follows the previous one). */
export function useTasks(instance: string, processId: string, options?: { enabled?: boolean; polling?: boolean }) {
  return useInstanceQuery(key(instance, 'process', 'item', processId, 'task'), () => fetchTasks(instance, processId), {
    enabled: options?.enabled,
    refetchInterval: (tasks) => (options?.polling || tasks?.some((task) => isRunning(task.state)) ? POLL_MS : false),
  })
}

export function useTask(instance: string, id: string, options?: { enabled?: boolean }) {
  return useInstanceQuery(key(instance, 'process', 'task', id), () => fetchTask(instance, id), options)
}

export const useCreateKnowledgeBase = mutationHook(createKnowledgeBase, ['knowledge'])
export const usePatchKnowledgeBase = mutationHook(patchKnowledgeBase, ['knowledge'])
export const useDeleteKnowledgeBase = mutationHook(deleteKnowledgeBase, ['knowledge', 'process'])
export const useReplaceKnowledgeGraph = mutationHook(replaceKnowledgeGraph, ['knowledge'])
export const useMergeKnowledgeGraph = mutationHook(mergeKnowledgeGraph, ['knowledge'])
export const useClearKnowledgeGraph = mutationHook(clearKnowledgeGraph, ['knowledge'])
export const useQueryKnowledge = mutationHook(queryKnowledge, [])
export const useSearchKnowledge = mutationHook(searchKnowledge, [])
export const useRecommendFromKnowledge = mutationHook(recommendFromKnowledge, [])
export const useSendRecommendationFeedback = mutationHook(sendRecommendationFeedback, [])
export const useUploadDocument = mutationHook(uploadDocument, ['knowledge'])
export const useDeleteDocument = mutationHook(deleteDocument, ['knowledge'])
export const useStartIngestion = mutationHook(startIngestion, ['knowledge', 'process'])
export const useDecideProposal = mutationHook(decideProposal, ['knowledge'])

export const useCreateModule = mutationHook(createModule, ['specification', 'term'])
export const usePatchModule = mutationHook(patchModule, ['specification', 'term'])
export const useDeleteModule = mutationHook(deleteModule, ['specification', 'term'])
export const useReplaceModuleContent = mutationHook(replaceModuleContent, ['specification', 'term'])
export const useCreateMapping = mutationHook(createMapping, ['specification', 'term'])
export const usePatchMapping = mutationHook(patchMapping, ['specification', 'term'])
export const useDeleteMapping = mutationHook(deleteMapping, ['specification', 'term'])
export const useCreateReview = mutationHook(createReview, ['specification', 'term'])
export const useRunValidation = mutationHook(runValidation, ['specification'])
export const useValidateDocument = mutationHook(validateDocument, [])
