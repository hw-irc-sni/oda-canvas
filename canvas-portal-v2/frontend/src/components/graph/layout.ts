// Deterministic node positions via @dagrejs/dagre: the same data always gives the same layout (no reshuffles on
// refresh). Independent of what the nodes are; d3 handles zoom and drag (useGraphViewport).

import dagre from '@dagrejs/dagre'

export interface Point {
  x: number
  y: number
}

export interface Bounds {
  width: number
  height: number
}

export interface LayoutNode {
  id: string
  width: number
  height: number
}

export interface LayoutEdge {
  id: string
  source: string
  target: string
  /** Space to keep for a label on the edge. */
  labelWidth?: number
  labelHeight?: number
}

/** x and y are the node's centre. */
export type Positioned<N> = N & Point

/** points are the polyline waypoints, endpoints included; label is the centre of the edge's label. */
export type Routed<E> = E & { points: Point[]; label?: Point }

export interface LayoutResult<N, E> {
  nodes: Positioned<N>[]
  edges: Routed<E>[]
  bounds: Bounds
}

export type LayoutDirection = 'TB' | 'LR' | 'BT' | 'RL'
export type LayoutDensity = 'compact' | 'comfortable' | 'spacious'

const LAYOUT_SPACING: Record<LayoutDensity, { nodesep: number; ranksep: number; edgesep: number; margin: number }> = {
  compact: { nodesep: 54, ranksep: 92, edgesep: 28, margin: 28 },
  comfortable: { nodesep: 104, ranksep: 138, edgesep: 42, margin: 44 },
  spacious: { nodesep: 154, ranksep: 178, edgesep: 58, margin: 58 },
}

export interface LayoutOptions {
  direction?: LayoutDirection
  density?: LayoutDensity
}

/** Lays out a directed graph. Every edge must join two of the nodes; parallel edges and self-loops are allowed. */
export function computeLayout<N extends LayoutNode, E extends LayoutEdge>(
  nodes: N[],
  edges: E[],
  { direction = 'TB', density = 'comfortable' }: LayoutOptions = {},
): LayoutResult<N, E> {
  const spacing = LAYOUT_SPACING[density]
  const g = new dagre.graphlib.Graph({ multigraph: true, compound: false })
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({
    rankdir: direction,
    nodesep: spacing.nodesep,
    ranksep: spacing.ranksep,
    edgesep: spacing.edgesep,
    marginx: spacing.margin,
    marginy: spacing.margin,
    // 'longest-path' lays things out faster; 'tight-tree' / 'network-simplex' look better but are slower.
    ranker: nodes.length > 250 ? 'longest-path' : 'network-simplex',
  })
  for (const node of nodes) g.setNode(node.id, { width: node.width, height: node.height })
  for (const edge of edges) {
    g.setEdge(
      edge.source,
      edge.target,
      { weight: 1, width: edge.labelWidth ?? 0, height: edge.labelHeight ?? 0, labelpos: 'c' },
      edge.id,
    )
  }

  dagre.layout(g)

  const rect = g.graph()
  return {
    nodes: nodes.map((node) => {
      const placed = g.node(node.id)
      return { ...node, x: placed?.x ?? 0, y: placed?.y ?? 0 }
    }),
    edges: edges.map((edge) => {
      const routed = g.edge({ v: edge.source, w: edge.target, name: edge.id }) as
        | { points?: Point[]; x?: number; y?: number }
        | undefined
      return {
        ...edge,
        points: (routed?.points ?? []).map((p) => ({ x: p.x, y: p.y })),
        label: routed?.x !== undefined && routed.y !== undefined ? { x: routed.x, y: routed.y } : undefined,
      }
    }),
    bounds: { width: rect.width ?? 1000, height: rect.height ?? 800 },
  }
}

/** Build a polyline path string for an edge, optionally rounded corners. */
export function edgePath(points: Point[], cornerRadius = 8): string {
  if (points.length === 0) return ''
  if (points.length === 1) return `M${points[0].x},${points[0].y}`
  if (points.length === 2 || cornerRadius <= 0) {
    return 'M' + points.map((p) => `${p.x},${p.y}`).join('L')
  }
  const segs: string[] = [`M${points[0].x},${points[0].y}`]
  for (let i = 1; i < points.length - 1; i++) {
    const a = points[i - 1]
    const b = points[i]
    const c = points[i + 1]
    const ab = Math.hypot(b.x - a.x, b.y - a.y)
    const bc = Math.hypot(c.x - b.x, c.y - b.y)
    const r = Math.min(cornerRadius, ab / 2, bc / 2)
    const ux1 = (b.x - a.x) / ab
    const uy1 = (b.y - a.y) / ab
    const ux2 = (c.x - b.x) / bc
    const uy2 = (c.y - b.y) / bc
    const p1 = { x: b.x - ux1 * r, y: b.y - uy1 * r }
    const p2 = { x: b.x + ux2 * r, y: b.y + uy2 * r }
    segs.push(`L${p1.x},${p1.y}`)
    segs.push(`Q${b.x},${b.y} ${p2.x},${p2.y}`)
  }
  const last = points[points.length - 1]
  segs.push(`L${last.x},${last.y}`)
  return segs.join(' ')
}
