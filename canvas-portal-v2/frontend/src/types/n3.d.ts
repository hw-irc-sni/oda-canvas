// The part of n3 that the portal uses. @types/n3 depends on @types/node, whose globals don't belong in browser code.
declare module 'n3' {
  export interface Term {
    termType: string
    value: string
    language?: string
    datatype?: { value: string }
  }
  export interface Quad {
    subject: Term
    predicate: Term
    object: Term
    graph: Term
  }
  export class Parser {
    constructor(options?: { format?: string; baseIRI?: string })
    parse(text: string): Quad[]
  }
}
