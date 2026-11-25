import type { Knowledge, KnowledgeCategory, KnowledgeSource, KnowledgeSourceKind, StoreKind } from '@/api/knowledge'
import type { useKnowledge } from '../KnowledgeContext'

type Access = ReturnType<typeof useKnowledge>

export function baseName(kb: Knowledge): string {
  return kb.name || kb.id
}

export const CATEGORIES: { value: KnowledgeCategory; label: string }[] = [
  { value: 'know-what', label: 'Know-what (facts)' },
  { value: 'know-why', label: 'Know-why (causes)' },
  { value: 'know-how', label: 'Know-how (procedures)' },
  { value: 'know-who', label: 'Know-who (people and roles)' },
]

export const STORE_KINDS: { value: StoreKind; label: string }[] = [
  { value: 'hybrid', label: 'Hybrid (graph and vectors)' },
  { value: 'graph', label: 'Graph only' },
  { value: 'vector', label: 'Vectors only' },
]

export const SOURCE_LABELS: Record<KnowledgeSourceKind, string> = {
  upload: 'Uploaded documents',
  tmf639: 'TMF639 Resource Inventory',
  tmf638: 'TMF638 Service Inventory',
  tmf642: 'TMF642 Alarm Management',
  tmf628: 'TMF628 Performance Management',
  tmf921: 'TMF921 Intent Management',
  remote: 'Remote URL',
}

export const SOURCE_KINDS = Object.keys(SOURCE_LABELS) as KnowledgeSourceKind[]

export function isEventSource(kind: KnowledgeSourceKind | undefined): boolean {
  return kind !== undefined && kind !== 'upload' && kind !== 'remote'
}

export function describeSource(source: KnowledgeSource): string {
  const details = [
    source.dependentApiName && `API ${source.dependentApiName}`,
    source.eventTypes?.length && source.eventTypes.join(', '),
    source.query && `filter ${source.query}`,
    source.url,
  ].filter(Boolean)
  const label = SOURCE_LABELS[source.kind] ?? source.kind
  return details.length ? `${label} (${details.join('; ')})` : label
}

/** Whether the knowledge-base has a vector store: documents can be searched. */
export function hasVectors(kb: Knowledge): boolean {
  return kb.storeKind !== 'graph'
}

/** The component refuses any change of a deprecated knowledge-base's fields and content. */
export function isWritable(kb: Knowledge): boolean {
  return kb.lifecycleStatus !== 'deprecated'
}

/**
 * Changing a knowledge-base's fields shares an operation with its lifecycle transitions, which reviewers and
 * approvers may call too: who may create knowledge-bases may edit them.
 */
export function mayEdit(access: Access, kb: Knowledge): boolean {
  return isWritable(kb) && access.can('createKnowledge') && access.can('patchKnowledge')
}
