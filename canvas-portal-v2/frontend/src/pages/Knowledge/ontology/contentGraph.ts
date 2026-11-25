// The content of an ontology module as a graph: its terms (classes, concepts, shapes, individuals) and what relates
// them. Properties are drawn as links from their domain to their range, OWL restrictions as links to what they restrict
// to. Pure, so that it can run on any parsed Turtle.

import type { Quad, Term } from 'n3'
import type { ValidationReport } from '@/api/knowledge'
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from '@/components/KnowledgeGraph'
import { localName } from '../kb/sparql'

const RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
const RDFS = 'http://www.w3.org/2000/01/rdf-schema#'
const OWL = 'http://www.w3.org/2002/07/owl#'
const SKOS = 'http://www.w3.org/2004/02/skos/core#'
const SH = 'http://www.w3.org/ns/shacl#'
const FTO = 'https://models.tmforum.org/ontology/fto/'
const DCTERMS = 'http://purl.org/dc/terms/'

const CLASSES = [`${OWL}Class`, `${RDFS}Class`]
const RELATIONS = [`${OWL}ObjectProperty`, `${RDF}Property`]
const ATTRIBUTES = [`${OWL}DatatypeProperty`]
const PROPERTIES = [...RELATIONS, ...ATTRIBUTES, `${OWL}AnnotationProperty`]
const LABELS = [`${RDFS}label`, `${SKOS}prefLabel`, `${DCTERMS}title`]
const DEFINITIONS = [`${SKOS}definition`, `${RDFS}comment`, `${DCTERMS}description`]
const HIERARCHY = [`${RDFS}subClassOf`, `${SKOS}broader`]

// Documentation and bookkeeping: listed in the details of a term, not drawn.
const NOT_DRAWN = new Set([
  `${RDF}type`,
  `${RDFS}isDefinedBy`,
  `${RDFS}seeAlso`,
  `${RDFS}domain`,
  `${RDFS}range`,
  `${OWL}imports`,
  `${SKOS}inScheme`,
  `${SKOS}topConceptOf`,
  `${FTO}layer`,
  `${FTO}linkStatus`,
])
const NOT_DRAWN_NAMESPACES = [
  DCTERMS,
  'http://purl.org/dc/elements/1.1/',
  'http://purl.org/vocab/vann/',
  'http://purl.org/vocommons/voaf#',
  'http://www.w3.org/2003/06/sw-vocab-status/ns#',
  'http://www.w3.org/ns/prov#',
  'http://creativecommons.org/ns#',
]
// The terms of these aren't what a module is about, even where it refers to them.
const LANGUAGE_NAMESPACES = [RDF, RDFS, OWL, SH, 'http://www.w3.org/2001/XMLSchema#']

const LAYER_OF: Record<string, string> = {
  [`${FTO}Foundational`]: 'Foundational',
  [`${FTO}Telecom`]: 'Telecom',
  [`${FTO}Implementation`]: 'Implementation',
}

/** Drawing more makes the layout slow and the graph unreadable: a neighbourhood shows the rest. */
export const MAX_NODES = 500
const MAX_NAMES = 2

export type TermKind = 'class' | 'concept' | 'shape' | 'individual' | 'property' | 'attribute' | 'ontology' | 'external'

export interface Finding {
  severity: 'error' | 'warning'
  text: string
}

export interface Statement {
  predicate: string
  text: string
  /** The term to open, when the value is one. */
  iri?: string
}

export interface ContentTerm {
  iri: string
  label: string
  definition?: string
  kind: TermKind
  typeLabel?: string
  layer?: string
  statements: Statement[]
  /** The properties whose domain it is. */
  properties: Statement[]
  findings: Finding[]
}

interface Relation {
  source: string
  target: string
  predicate: string
  hierarchy: boolean
  /** A restriction: what the class requires, rather than a statement about the term. */
  tentative: boolean
}

export interface ModuleContent {
  terms: Map<string, ContentTerm>
  relations: Relation[]
  /** The IRIs the content declares as owl:Ontology. */
  ontologies: string[]
  /** The properties that can't be drawn: no domain, or no range. */
  loose: string[]
}

const isNamed = (term: Term) => term.termType === 'NamedNode'
const inNamespace = (iri: string, namespaces: string[]) => namespaces.some((ns) => iri.startsWith(ns))

export function readContent(quads: Quad[]): ModuleContent {
  const bySubject = new Map<string, Quad[]>()
  // Blank node labels and IRIs never clash: an absolute IRI has a colon, n3's blank nodes have none in front.
  const key = (term: Term) => (isNamed(term) ? term.value : `_:${term.value}`)
  for (const quad of quads) {
    const id = key(quad.subject)
    const list = bySubject.get(id)
    if (list) list.push(quad)
    else bySubject.set(id, [quad])
  }
  const objects = (subject: string, ...predicates: string[]) =>
    (bySubject.get(subject) ?? []).filter((q) => predicates.includes(q.predicate.value)).map((q) => q.object)
  const has = (subject: string, predicate: string, ...values: string[]) =>
    objects(subject, predicate).some((o) => values.includes(o.value))

  function text(subject: string, predicates: string[]): string | undefined {
    for (const predicate of predicates) {
      const values = objects(subject, predicate).filter((o) => o.termType === 'Literal')
      const value = values.find((o) => o.language?.startsWith('en')) ?? values.find((o) => !o.language) ?? values[0]
      if (value?.value.trim()) return value.value.trim()
    }
    return undefined
  }

  function listItems(head: Term): Term[] {
    const items: Term[] = []
    const seen = new Set<string>()
    let node = head
    while (node.value !== `${RDF}nil` && !seen.has(key(node))) {
      seen.add(key(node))
      items.push(...objects(key(node), `${RDF}first`))
      const rest = objects(key(node), `${RDF}rest`)[0]
      if (!rest) break
      node = rest
    }
    return items
  }

  const MAX_DEPTH = 6

  /** The named classes of an expression: itself, or the members of a union or an intersection. */
  function namedClasses(term: Term, depth = 0): string[] {
    if (isNamed(term)) return [term.value]
    if (term.termType !== 'BlankNode' || depth > MAX_DEPTH) return []
    return objects(key(term), `${OWL}unionOf`, `${OWL}intersectionOf`)
      .flatMap(listItems)
      .flatMap((member) => namedClasses(member, depth + 1))
  }

  /** The restrictions of an expression, as property and class. */
  function restrictions(term: Term, depth = 0): { property: string; target: string }[] {
    if (term.termType !== 'BlankNode' || depth > MAX_DEPTH) return []
    const id = key(term)
    const property = objects(id, `${OWL}onProperty`).find(isNamed)?.value
    if (property) {
      return objects(id, `${OWL}someValuesFrom`, `${OWL}allValuesFrom`, `${OWL}onClass`, `${OWL}hasValue`)
        .flatMap((filler) => namedClasses(filler))
        .map((target) => ({ property, target }))
    }
    return objects(id, `${OWL}unionOf`, `${OWL}intersectionOf`)
      .flatMap(listItems)
      .flatMap((member) => restrictions(member, depth + 1))
  }

  /** A value in words: an OWL expression is written out. */
  function describe(term: Term, depth = 0): string {
    if (isNamed(term)) return text(term.value, LABELS) ?? localName(term.value)
    if (term.termType === 'Literal') return term.language ? `${term.value} @${term.language}` : term.value
    if (term.termType !== 'BlankNode' || depth > MAX_DEPTH) return '…'
    const id = key(term)
    const property = objects(id, `${OWL}onProperty`)[0]
    if (property) {
      const name = describe(property, depth + 1)
      const filler = (predicate: string) => objects(id, predicate)[0]
      const on = filler(`${OWL}onClass`) ?? filler(`${OWL}onDataRange`)
      const words: [string, string][] = [
        ['someValuesFrom', 'some'],
        ['allValuesFrom', 'only'],
        ['hasValue', 'value'],
        ['cardinality', 'exactly'],
        ['qualifiedCardinality', 'exactly'],
        ['minCardinality', 'at least'],
        ['minQualifiedCardinality', 'at least'],
        ['maxCardinality', 'at most'],
        ['maxQualifiedCardinality', 'at most'],
      ]
      for (const [predicate, word] of words) {
        const value = filler(`${OWL}${predicate}`)
        if (value) return [name, word, describe(value, depth + 1), on && describe(on, depth + 1)].filter(Boolean).join(' ')
      }
      return name
    }
    for (const [predicate, word] of [['unionOf', ' or '], ['intersectionOf', ' and ']]) {
      const list = objects(id, `${OWL}${predicate}`)[0]
      if (list) return `(${listItems(list).map((m) => describe(m, depth + 1)).join(word)})`
    }
    const complement = objects(id, `${OWL}complementOf`)[0]
    if (complement) return `not ${describe(complement, depth + 1)}`
    const path = objects(id, `${SH}path`)[0]
    if (path) return describe(path, depth + 1)
    return '(unnamed)'
  }

  const terms = new Map<string, ContentTerm>()
  const typesOf = (iri: string) => objects(iri, `${RDF}type`).filter(isNamed).map((t) => t.value)
  const isReified = (iri: string) => {
    const names = new Set((bySubject.get(iri) ?? []).map((q) => localName(q.predicate.value)))
    return names.has('subject') && names.has('predicate') && names.has('object')
  }

  function kindOf(iri: string): TermKind {
    const types = typesOf(iri)
    const is = (list: string[]) => types.some((t) => list.includes(t))
    if (is([`${OWL}Ontology`])) return 'ontology'
    if (is(CLASSES)) return 'class'
    if (is(ATTRIBUTES)) return 'attribute'
    if (is(PROPERTIES)) return 'property'
    if (is([`${SKOS}Concept`])) return 'concept'
    if (is([`${SH}NodeShape`, `${SH}PropertyShape`])) return 'shape'
    return types.some((t) => !inNamespace(t, LANGUAGE_NAMESPACES)) ? 'individual' : 'external'
  }

  function term(iri: string): ContentTerm {
    const known = terms.get(iri)
    if (known) return known
    const kind = kindOf(iri)
    const type = typesOf(iri).find((t) => !inNamespace(t, LANGUAGE_NAMESPACES))
    const layer = objects(iri, `${FTO}layer`)[0]?.value
    const created: ContentTerm = {
      iri,
      label: text(iri, LABELS) ?? localName(iri),
      definition: text(iri, DEFINITIONS),
      kind,
      typeLabel: kind === 'individual' && type ? localName(type) : kind,
      layer: layer ? LAYER_OF[layer] : undefined,
      statements: (bySubject.get(iri) ?? []).map((q) => ({
        predicate: q.predicate.value,
        text: describe(q.object),
        iri: isNamed(q.object) ? q.object.value : undefined,
      })),
      properties: [],
      findings: [],
    }
    terms.set(iri, created)
    return created
  }

  const relations: Relation[] = []
  const seen = new Set<string>()
  function relate(source: string, target: string, predicate: string, options: { hierarchy?: boolean; tentative?: boolean } = {}) {
    if (inNamespace(target, LANGUAGE_NAMESPACES) || inNamespace(source, LANGUAGE_NAMESPACES)) return
    const id = `${source} ${predicate} ${target} ${options.tentative ?? false}`
    if (seen.has(id)) return
    seen.add(id)
    term(source)
    term(target)
    relations.push({ source, target, predicate, hierarchy: options.hierarchy ?? false, tentative: options.tentative ?? false })
  }

  const ontologies: string[] = []
  const loose: string[] = []
  for (const [subject, statements] of bySubject) {
    if (subject.startsWith('_:')) continue
    const kind = kindOf(subject)
    if (kind === 'ontology') {
      ontologies.push(subject)
      continue
    }
    // The bridge's record of a mapping: the mapping itself is also stated, and drawn as a link.
    if (isReified(subject)) continue

    if (kind === 'property' || kind === 'attribute') {
      const self = term(subject)
      const domains = objects(subject, `${RDFS}domain`).flatMap((o) => namedClasses(o))
      const ranges = objects(subject, `${RDFS}range`)
      const rangeClasses = ranges.flatMap((o) => namedClasses(o)).filter((iri) => !inNamespace(iri, LANGUAGE_NAMESPACES))
      for (const domain of new Set(domains)) {
        term(domain).properties.push({
          predicate: subject,
          iri: subject,
          text: ranges.length ? `${self.label}: ${ranges.map((r) => describe(r)).join(', ')}` : self.label,
        })
        for (const range of new Set(rangeClasses)) relate(domain, range, self.label)
      }
      // An attribute with a domain is shown with its class.
      if (domains.length === 0 || (kind === 'property' && rangeClasses.length === 0)) loose.push(subject)
      continue
    }

    if (kind !== 'external') term(subject)
    for (const { predicate, object } of statements) {
      const iri = predicate.value
      if (iri === `${RDF}type`) {
        if (kind === 'individual' && isNamed(object) && kindOf(object.value) === 'class') relate(subject, object.value, 'is a')
        continue
      }
      if (NOT_DRAWN.has(iri) || inNamespace(iri, NOT_DRAWN_NAMESPACES)) continue
      const name = text(iri, LABELS) ?? localName(iri)
      if (isNamed(object)) {
        relate(subject, object.value, name, { hierarchy: HIERARCHY.includes(iri) })
      } else if (object.termType === 'BlankNode') {
        if (iri === `${SH}property`) {
          const path = objects(key(object), `${SH}path`)[0]
          for (const target of objects(key(object), `${SH}class`, `${SH}node`).filter(isNamed)) {
            relate(subject, target.value, path ? describe(path) : name)
          }
          continue
        }
        for (const { property, target } of restrictions(object)) {
          relate(subject, target, text(property, LABELS) ?? localName(property), { tentative: true })
        }
        // A defined class: its named parts.
        if ([`${OWL}equivalentClass`, `${RDFS}subClassOf`, `${OWL}disjointWith`].includes(iri)) {
          for (const target of namedClasses(object)) relate(subject, target, name, { tentative: true })
        }
      } else continue
    }
    // Parts written directly on the class, rather than through an expression.
    for (const predicate of ['unionOf', 'intersectionOf', 'disjointUnionOf', 'oneOf']) {
      for (const member of objects(subject, `${OWL}${predicate}`).flatMap(listItems)) {
        if (isNamed(member)) relate(subject, member.value, predicate)
      }
    }
  }
  return { terms, relations, ontologies, loose }
}

// ---- validation ----------------------------------------------------------------------------------------------

/** The findings of a validation, by the term they are about; those about the whole module apart. */
export function placeFindings(report: ValidationReport | null | undefined, content: ModuleContent) {
  const byTerm = new Map<string, Finding[]>()
  const onModule: Finding[] = []
  const iris = [...content.terms.keys()]
  const byLocalName = (name: string) => {
    const found = iris.filter((iri) => localName(iri) === name)
    // The validation writes local names in some messages: one that several terms share says nothing about which.
    return found.length === 1 ? found : []
  }
  const place = (targets: string[], finding: Finding) => {
    const known = targets.filter((iri) => content.terms.has(iri))
    if (known.length === 0) onModule.push(finding)
    for (const iri of known) byTerm.set(iri, [...(byTerm.get(iri) ?? []), finding])
  }
  for (const rule of report?.rules ?? []) {
    for (const message of rule.messages ?? []) {
      let targets: string[] = []
      if (rule.rule === 'parentsResolve') {
        const match = /^parent (\S+) of (\S+) is not declared$/.exec(message.text)
        targets = match ? [match[1], match[2]] : []
      } else if (rule.rule === 'layerAnnotation') {
        const match = /^class (\S+) is tagged /.exec(message.text)
        targets = match ? byLocalName(match[1]) : []
      } else if (rule.rule === 'layerMonotonicity') {
        const match = /^(\S+) \(\w+\) extends deeper-layer (\S+) \(/.exec(message.text)
        targets = match ? [...byLocalName(match[1]), ...byLocalName(match[2])] : []
      }
      place(targets, { severity: message.severity, text: message.text })
    }
  }
  for (const result of report?.shacl?.results ?? []) {
    const path = result.resultPath ? `${localName(result.resultPath)}: ` : ''
    place(result.focusNode ? [result.focusNode] : [], {
      severity: result.severity === 'Violation' ? 'error' : 'warning',
      text: `SHACL: ${path}${result.message ?? 'violates a shape'}`,
    })
  }
  return { byTerm, onModule }
}

// ---- drawing -------------------------------------------------------------------------------------------------

export interface DrawOptions {
  /** Links other than the hierarchy. */
  relations: boolean
  /** Terms the module refers to without declaring them. */
  external: boolean
  /** Only this term and all it is linked to, whatever the other options. */
  around?: string
  findings: Map<string, Finding[]>
}

const KIND_NAMES: Record<TermKind, string> = {
  class: 'Class',
  concept: 'Concept',
  shape: 'Shape',
  individual: 'Individual',
  property: 'Property',
  attribute: 'Attribute',
  ontology: 'Ontology',
  external: 'Not declared here',
}

export function kindName(term: ContentTerm): string {
  return term.kind === 'individual' && term.typeLabel ? term.typeLabel : KIND_NAMES[term.kind]
}

/** Whether a module is better opened on its hierarchy: it has one, and all its links together are hard to read. */
export function startsOnHierarchy({ relations }: ModuleContent): boolean {
  return relations.length > 60 && relations.filter((r) => r.hierarchy).length * 3 >= relations.length
}

export function drawContent(
  content: ModuleContent,
  { relations, external, around, findings }: DrawOptions,
): { nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[]; cut: number } {
  const links = around
    ? content.relations.filter((r) => r.source === around || r.target === around)
    : content.relations.filter(
        (r) => (relations || r.hierarchy) && (external || (!isExternal(content, r.source) && !isExternal(content, r.target))),
      )

  const degree = new Map<string, number>()
  for (const { source, target } of links) {
    degree.set(source, (degree.get(source) ?? 0) + 1)
    degree.set(target, (degree.get(target) ?? 0) + 1)
  }
  const wanted = [...content.terms.values()].filter((t) => {
    if (around) return t.iri === around || degree.has(t.iri)
    if (degree.has(t.iri)) return true
    // A term linked to nothing is still part of the module; properties are links, or listed apart.
    return t.kind !== 'property' && t.kind !== 'attribute' && t.kind !== 'ontology' && t.kind !== 'external'
  })
  // What a reviewer must not miss stays when the graph is cut: the findings, then the most linked terms.
  const rank = (t: ContentTerm) => (t.iri === around ? 2e6 : 0) + (findings.has(t.iri) ? 1e6 : 0) + (degree.get(t.iri) ?? 0)
  const shown = wanted.length > MAX_NODES ? [...wanted].sort((a, b) => rank(b) - rank(a)).slice(0, MAX_NODES) : wanted
  const ids = new Set(shown.map((t) => t.iri))

  // Several properties between the same two terms are one link: separate ones would hide each other's names.
  const merged = new Map<string, { edge: KnowledgeGraphEdge; names: string[] }>()
  for (const r of links) {
    if (!ids.has(r.source) || !ids.has(r.target)) continue
    const id = `${r.source} ${r.target} ${r.tentative} ${r.hierarchy ? r.predicate : ''}`
    const known = merged.get(id)
    if (known) known.names.push(r.predicate)
    else merged.set(id, { edge: { source: r.source, target: r.target, predicate: r.predicate, tentative: r.tentative }, names: [r.predicate] })
  }
  return {
    cut: wanted.length - shown.length,
    nodes: shown.map((t) => {
      const problems = findings.get(t.iri)
      return {
        iri: t.iri,
        label: t.label,
        type: kindName(t),
        layer: t.layer,
        caption: t.kind === 'external' ? hostOf(t.iri) : t.properties.length ? `${t.properties.length} ${t.properties.length === 1 ? 'property' : 'properties'}` : localName(t.iri),
        tentative: t.kind === 'external',
        flag: problems?.some((f) => f.severity === 'error') ? 'error' : problems?.length ? 'warning' : undefined,
      }
    }),
    edges: [...merged.values()].map(({ edge, names }) => ({
      ...edge,
      predicate: names.length > MAX_NAMES ? `${names[0]} +${names.length - 1}` : names.join(', '),
    })),
  }
}

function isExternal(content: ModuleContent, iri: string): boolean {
  return content.terms.get(iri)?.kind === 'external'
}

function hostOf(iri: string): string {
  return iri.replace(/^[a-z]+:\/\//i, '').replace(/[^/#]*$/, '').replace(/[/#]$/, '') || iri
}
