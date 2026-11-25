// Layout of a graph whose nodes belong to ordered bands, such as the ontology layers: one band per row, its nodes in
// a grid, and each node placed near the nodes it's linked to in the previous band.

import type { Bounds, LayoutEdge, LayoutNode, Point, Positioned, Routed } from '@/components/graph/layout'

const GAP_X = 34
const GAP_Y = 46
const BAND_GAP = 64
const BAND_PAD = { x: 28, top: 46, bottom: 28 }
const MARGIN = 24

export interface Band {
  name: string
  x: number
  y: number
  width: number
  height: number
}

export interface BandLayoutResult<N, E> {
  nodes: Positioned<N>[]
  edges: Routed<E>[]
  bands: Band[]
  bounds: Bounds
}

export function computeBandLayout<N extends LayoutNode & { band: string }, E extends LayoutEdge>(
  nodes: N[],
  edges: E[],
  order: string[],
): BandLayoutResult<N, E> {
  const names = [...order.filter((name) => nodes.some((n) => n.band === name))]
  for (const node of nodes) if (!names.includes(node.band)) names.push(node.band)

  const neighbours = new Map<string, string[]>()
  const link = (a: string, b: string) => neighbours.set(a, [...(neighbours.get(a) ?? []), b])
  for (const edge of edges) {
    link(edge.source, edge.target)
    link(edge.target, edge.source)
  }

  // About as wide as a screen: a band wraps instead of growing sideways.
  const largest = Math.max(0, ...names.map((name) => nodes.filter((n) => n.band === name).length))
  const columns = Math.max(4, Math.min(9, Math.ceil(Math.sqrt(largest * 3.5))))
  const cellWidth = Math.max(0, ...nodes.map((n) => n.width))
  const innerWidth = columns * cellWidth + (columns - 1) * GAP_X
  const bandWidth = innerWidth + BAND_PAD.x * 2

  const placed = new Map<string, Positioned<N>>()
  const bands: Band[] = []
  let y = MARGIN
  for (const name of names) {
    const members = nodes.filter((n) => n.band === name)
    const centre = (node: N) => {
      const xs = (neighbours.get(node.id) ?? []).map((id) => placed.get(id)?.x).filter((x): x is number => x !== undefined)
      return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : Infinity
    }
    const sorted = members
      .map((node) => ({ node, x: centre(node) }))
      .sort((a, b) => (a.x === b.x ? a.node.id.localeCompare(b.node.id) : a.x - b.x))
      .map(({ node }) => node)
    const rows = Math.ceil(sorted.length / columns)
    let rowY = y + BAND_PAD.top
    for (let row = 0; row < rows; row++) {
      // Filled column by column, so that neighbours in the order stay neighbours in x.
      const inRow = sorted.filter((_, i) => i % rows === row)
      const rowHeight = Math.max(...inRow.map((n) => n.height))
      const rowWidth = inRow.length * cellWidth + (inRow.length - 1) * GAP_X
      inRow.forEach((node, i) => {
        const x = MARGIN + BAND_PAD.x + (innerWidth - rowWidth) / 2 + cellWidth / 2 + i * (cellWidth + GAP_X)
        placed.set(node.id, { ...node, x, y: rowY + rowHeight / 2 })
      })
      rowY += rowHeight + GAP_Y
    }
    const height = rowY - GAP_Y + BAND_PAD.bottom - y
    bands.push({ name, x: MARGIN, y, width: bandWidth, height })
    y += height + BAND_GAP
  }

  return {
    nodes: nodes.map((node) => placed.get(node.id)!),
    edges: edges.map((edge) => ({ ...edge, points: route(placed.get(edge.source)!, placed.get(edge.target)!) })),
    bands,
    bounds: { width: bandWidth + MARGIN * 2, height: Math.max(0, y - BAND_GAP) + MARGIN },
  }
}

/** Endpoints and two control points of a curve: between rows vertically, inside a row as an arc above it. */
function route(source: Positioned<LayoutNode>, target: Positioned<LayoutNode>): Point[] {
  if (source.y === target.y) {
    const top = { s: source.y - source.height / 2, t: target.y - target.height / 2 }
    const lift = GAP_Y * 0.8
    return [
      { x: source.x, y: top.s },
      { x: source.x, y: top.s - lift },
      { x: target.x, y: top.t - lift },
      { x: target.x, y: top.t },
    ]
  }
  const down = target.y > source.y
  const start = { x: source.x, y: source.y + (down ? 1 : -1) * (source.height / 2) }
  const end = { x: target.x, y: target.y - (down ? 1 : -1) * (target.height / 2) }
  const bend = Math.abs(end.y - start.y) / 2
  return [
    start,
    { x: start.x, y: start.y + (down ? bend : -bend) },
    { x: end.x, y: end.y - (down ? bend : -bend) },
    end,
  ]
}

export function curvePath(points: Point[]): string {
  if (points.length !== 4) return ''
  const [a, b, c, d] = points
  return `M${a.x},${a.y}C${b.x},${b.y} ${c.x},${c.y} ${d.x},${d.y}`
}
