export interface KnowledgeGraphNode {
  /** The node's identity: an IRI, or any unique id (a blank node, a literal, a module id). */
  iri: string
  label: string
  /** Shown above the label, and the node's colour when it has no layer. */
  type?: string
  /** One of the bands, when the graph has bands; also the node's colour. */
  layer?: string
  /** Shown under the label. */
  caption?: string
  /** Dashed outline, for something not (yet) agreed. */
  tentative?: boolean
}

export interface KnowledgeGraphEdge {
  source: string
  target: string
  predicate: string
  /** Dashed line. */
  tentative?: boolean
}
