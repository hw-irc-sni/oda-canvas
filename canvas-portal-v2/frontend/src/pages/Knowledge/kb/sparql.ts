import type { SparqlResults, SparqlTerm } from '@/api/knowledge'
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from '@/components/KnowledgeGraph'

/** An absolute IRI that can be written between angle brackets in a query. */
export function isIri(value: string): boolean {
  return /^[A-Za-z][A-Za-z0-9+.-]*:[^\s<>"{}|^`\\]+$/.test(value)
}

/** A SPARQL string literal. */
function literal(value: string): string {
  return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n').replace(/\r/g, '\\r')}"`
}

const RDF_TYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'
const RDFS_LABEL = 'http://www.w3.org/2000/01/rdf-schema#label'
const PROV = 'http://www.w3.org/ns/prov#'

export const ENTITY_LIMIT = 100
export const NEIGHBOUR_LIMIT = 200

/** The last part of an IRI. */
export function localName(iri: string): string {
  return iri.replace(/[/#]+$/, '').split(/[/#:]/).pop() || iri
}

// A query sees the knowledge-base's graph together with the modules it conforms to: what describes the ontology, and
// the provenance records of the pipeline, aren't entities.
const NOT_ENTITIES = [
  'http://www.w3.org/2002/07/owl#',
  'http://www.w3.org/2000/01/rdf-schema#',
  'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  'http://www.w3.org/ns/shacl#',
  'http://www.w3.org/2004/02/skos/core#',
  PROV,
]

/** The entities of a knowledge-base whose label or IRI contains a text. */
export function entitiesQuery(text: string): string {
  const needle = text.trim().toLowerCase()
  return `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?entity (SAMPLE(?l) AS ?label) (SAMPLE(?t) AS ?type) WHERE {
  ?entity a ?t .
  FILTER(isIRI(?entity))
  ${NOT_ENTITIES.map((ns) => `FILTER(!STRSTARTS(STR(?t), ${literal(ns)}))`).join('\n  ')}
  OPTIONAL { ?entity rdfs:label ?l }
  ${needle ? `FILTER(CONTAINS(LCASE(STR(?entity)), ${literal(needle)}) || CONTAINS(LCASE(STR(?l)), ${literal(needle)}))` : ''}
} GROUP BY ?entity ORDER BY ?entity LIMIT ${ENTITY_LIMIT}`
}

/** The statements about an entity, in both directions, with the labels and types of what it's linked to. */
export function neighbourhoodQuery(iri: string): string {
  if (!isIri(iri)) throw new Error('Not an IRI')
  return `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?s ?p ?o ?otherLabel ?otherType WHERE {
  { BIND(<${iri}> AS ?s) <${iri}> ?p ?o . BIND(?o AS ?other) }
  UNION
  { BIND(<${iri}> AS ?o) ?s ?p <${iri}> . BIND(?s AS ?other) }
  OPTIONAL { ?other rdfs:label ?otherLabel }
  OPTIONAL { ?other a ?otherType }
} LIMIT ${NEIGHBOUR_LIMIT}`
}

export interface Statement {
  s: SparqlTerm
  p: SparqlTerm
  o: SparqlTerm
}

export interface Neighbourhood {
  statements: Statement[]
  labels: Record<string, string>
  types: Record<string, string>
  /** True when there may be more statements than the query returns. */
  cut: boolean
}

export function toNeighbourhood(iri: string, results: SparqlResults): Neighbourhood {
  const rows = results.results?.bindings ?? []
  const seen = new Map<string, Statement>()
  const labels: Record<string, string> = {}
  const types: Record<string, string> = {}
  for (const { s, p, o, otherLabel, otherType } of rows) {
    if (!s || !p || !o) continue
    // Several labels or types of the other end repeat the statement.
    seen.set(JSON.stringify([s.value, p.value, o.type, o.value, o.datatype, o['xml:lang']]), { s, p, o })
    const other = s.value === iri ? o : s
    if (other.type === 'uri') {
      if (otherLabel) labels[other.value] ??= otherLabel.value
      if (otherType?.type === 'uri') types[other.value] ??= otherType.value
    }
    if (s.value === iri && p.value === RDFS_LABEL) labels[iri] ??= o.value
    if (s.value === iri && p.value === RDF_TYPE && o.type === 'uri') types[iri] ??= o.value
  }
  return { statements: [...seen.values()], labels, types, cut: rows.length >= NEIGHBOUR_LIMIT }
}

/** The graph of the neighbourhoods seen so far. Types are shown on the nodes, literals in the details. */
export function toGraph(
  neighbourhoods: Neighbourhood[],
  { showProvenance }: { showProvenance: boolean },
): { nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[] } {
  const labels: Record<string, string> = Object.assign({}, ...neighbourhoods.map((n) => n.labels))
  const types: Record<string, string> = Object.assign({}, ...neighbourhoods.map((n) => n.types))
  const nodes = new Map<string, KnowledgeGraphNode>()
  const edges = new Map<string, KnowledgeGraphEdge>()
  const add = (iri: string) => {
    if (nodes.has(iri)) return
    nodes.set(iri, {
      iri,
      label: labels[iri] ?? localName(iri),
      type: types[iri] ? localName(types[iri]) : undefined,
      caption: iri,
    })
  }
  for (const { statements } of neighbourhoods) {
    for (const { s, p, o } of statements) {
      if (s.type !== 'uri' || o.type !== 'uri' || p.value === RDF_TYPE) continue
      if (!showProvenance && p.value.startsWith(PROV)) continue
      add(s.value)
      add(o.value)
      edges.set(`${s.value} ${p.value} ${o.value}`, { source: s.value, target: o.value, predicate: localName(p.value) })
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] }
}

/** How a value is shown in a table. */
export function termText(term: SparqlTerm): string {
  if (term.type === 'bnode') return `_:${term.value}`
  return term.value
}

export function termDetail(term: SparqlTerm): string | undefined {
  if (term['xml:lang']) return `@${term['xml:lang']}`
  if (term.datatype && term.datatype !== 'http://www.w3.org/2001/XMLSchema#string') return localName(term.datatype)
  return undefined
}
