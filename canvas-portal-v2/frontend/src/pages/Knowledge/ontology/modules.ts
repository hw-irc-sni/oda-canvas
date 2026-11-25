import type { KnowledgeSpecification, KnowledgeSpecificationRef, KnowledgeSpecificationSource } from '@/api/knowledge'
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from '@/components/KnowledgeGraph'
import { statusLabel } from '../shared'

/** A module governed by its federation repository: read-only through the API. */
export function isFederated(module: KnowledgeSpecification): boolean {
  return module['@type'] === 'FederatedKnowledgeSpecification'
}

export function moduleName(module: KnowledgeSpecification): string {
  return module.name || module.id
}

/** The registered module a reference names, by id or by IRI. */
export function resolveRef(
  ref: KnowledgeSpecificationRef,
  modules: KnowledgeSpecification[],
): KnowledgeSpecification | undefined {
  return modules.find((m) => m.id === ref.id) ?? (ref.iri ? modules.find((m) => m.iri === ref.iri) : undefined)
}

export function describeSource(source: KnowledgeSpecificationSource | undefined): string {
  switch (source?.kind) {
    case 'upload':
      return 'Uploaded'
    case 'git':
      return `Git: ${source.url} (${source.ref || 'main'}) ${source.path}`
    case 'mount':
      return `Mounted: ${source.path}`
    case 'componentContribution':
      return `Contributed by ${source.componentName || source.componentId || 'a component'}`
    case 'remoteEndpoint':
      return `${source.endpointKind === 'tmfc060' ? 'Peer TMFC060' : 'SPARQL endpoint'}: ${source.url}`
    default:
      return '—'
  }
}

/** The modules and the imports between them. */
export function moduleGraph(modules: KnowledgeSpecification[]): {
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
} {
  const edges: KnowledgeGraphEdge[] = []
  for (const module of modules) {
    for (const ref of module.imports ?? []) {
      const target = resolveRef(ref, modules)
      if (target) edges.push({ source: module.id, target: target.id, predicate: 'imports' })
    }
    for (const ref of module.describes ?? []) {
      const target = resolveRef(ref, modules)
      if (target) edges.push({ source: module.id, target: target.id, predicate: 'describes', tentative: true })
    }
  }
  return {
    nodes: modules.map((module) => ({
      iri: module.id,
      label: moduleName(module),
      type: module.specKind,
      layer: module.layer,
      caption: [module.prefix, module.version, statusLabel(module.lifecycleStatus)].filter(Boolean).join(' · '),
      tentative: module.lifecycleStatus !== 'published',
    })),
    edges,
  }
}
