import React, { useId, useMemo, useState } from 'react'
import { Empty, Space, Tag } from 'antd'

import { downloadBlob, svgSource, timestamp } from '@/components/graph/export'
import GraphToolbar from '@/components/graph/GraphToolbar'
import { computeLayout, edgePath, type LayoutDirection, type Point, type Positioned } from '@/components/graph/layout'
import { emptySurfaceStyle, graphContainerStyle, graphSurfaceStyle, truncate } from '@/components/graph/surface'
import { useGraphViewport } from '@/components/graph/useGraphViewport'
import { colors } from '@/theme'
import { computeBandLayout, curvePath, type Band } from './band-layout'
import { EDGE_ACTIVE_COLOR, EDGE_COLOR, graphColor } from './style'
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from './types'

export type { KnowledgeGraphEdge, KnowledgeGraphNode } from './types'

const NODE_WIDTH = 218
const NODE_HEIGHT = 66
const PADDING = 36
const MIN_ZOOM = 0.05
const MAX_ZOOM = 2.6
const LABEL_CHARS = 26
const PREDICATE_CHARS = 24
const NO_BAND = 'Other'

interface KnowledgeGraphProps {
  nodes: KnowledgeGraphNode[]
  /** Edges to nodes that aren't in the graph are left out. */
  edges: KnowledgeGraphEdge[]
  onSelect?: (node: KnowledgeGraphNode) => void
  /** The selected node's edges are highlighted. */
  selected?: string
  direction?: LayoutDirection
  /** Draws one band per layer in this order, top to bottom, instead of the directed layout. */
  bands?: string[]
  height?: number
  /** Shown left of the toolbar. */
  title?: React.ReactNode
  emptyText?: string
  /** Used in the exported file's name. */
  exportName?: string
}

interface DrawnEdge extends KnowledgeGraphEdge {
  id: string
  path: string
  label?: Point
}

/** A directed graph of RDF nodes and predicates: pan, zoom, select, full screen and SVG export. */
export default function KnowledgeGraph({
  nodes,
  edges,
  onSelect,
  selected,
  direction = 'LR',
  bands,
  height = 520,
  title,
  emptyText = 'Nothing to show',
  exportName = 'knowledge-graph',
}: KnowledgeGraphProps) {
  const [hovered, setHovered] = useState<string>()
  // Marker and pattern ids are global in a page, which can hold several graphs.
  const uid = useId().replace(/[^A-Za-z0-9]/g, '')
  const bandKey = bands?.join('|')
  const layout = useMemo(() => layoutGraph(nodes, edges, direction, bands), [nodes, edges, direction, bandKey])
  const layoutKey = useMemo(() => layout.nodes.map((node) => node.id).join('|'), [layout])
  const view = useGraphViewport({
    hasNodes: layout.nodes.length > 0,
    bounds: layout.bounds,
    layoutKey,
    height,
    minWidth: 320,
    padding: PADDING,
    minZoom: MIN_ZOOM,
    maxZoom: MAX_ZOOM,
    maxFitScale: 1.15,
  })

  const focus = hovered ?? selected
  const focusExists = focus !== undefined && layout.nodes.some((node) => node.id === focus)
  const isActive = (edge: KnowledgeGraphEdge) => focusExists && (edge.source === focus || edge.target === focus)
  const near = useMemo(() => {
    if (!focusExists) return null
    const ids = new Set([focus])
    for (const edge of layout.edges) {
      if (edge.source === focus) ids.add(edge.target)
      if (edge.target === focus) ids.add(edge.source)
    }
    return ids
  }, [focus, focusExists, layout])

  function downloadSvg() {
    const svgEl = view.svgRef.current
    if (!svgEl) return
    downloadBlob(
      svgSource(svgEl, layout.bounds, { resetView: true }),
      `${exportName}-${timestamp()}.svg`,
      'image/svg+xml;charset=utf-8',
    )
  }

  // Active edges are drawn last, over the others.
  const drawn = [...layout.edges].sort((a, b) => Number(isActive(a)) - Number(isActive(b)))

  return (
    <div style={graphContainerStyle(view.isFullscreen)}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 10,
          marginBottom: view.isFullscreen ? 0 : 10,
        }}
      >
        <Space size={8} wrap>
          {title}
          <Tag color="cyan">{layout.nodes.length} nodes</Tag>
          <Tag color="geekblue">{layout.edges.length} links</Tag>
        </Space>
        <GraphToolbar
          exports={[{ key: 'svg', label: 'Download SVG (image)', onClick: downloadSvg }]}
          exportDisabled={layout.nodes.length === 0}
          isFullscreen={view.isFullscreen}
          onToggleFullscreen={view.toggleFullscreen}
          onZoom={view.zoomBy}
          onFit={view.fitToScreen}
        />
      </div>

      <div ref={view.wrapperRef} style={graphSurfaceStyle(view.isFullscreen, height)}>
        {layout.nodes.length === 0 ? (
          <div style={emptySurfaceStyle}>
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} />
          </div>
        ) : (
          <svg
            ref={view.svgRef}
            viewBox={`0 0 ${view.viewportWidth} ${view.viewportHeight}`}
            preserveAspectRatio="xMidYMid meet"
            style={{ display: 'block', width: '100%', height: '100%', cursor: 'grab' }}
          >
            <defs>
              {[
                { id: `arrow-${uid}`, color: EDGE_COLOR },
                { id: `arrow-active-${uid}`, color: EDGE_ACTIVE_COLOR },
              ].map(({ id, color }) => (
                <marker key={id} id={id} viewBox="0 -5 10 10" refX="9" refY="0" markerWidth={8} markerHeight={8} orient="auto">
                  <path d="M0,-5L10,0L0,5" fill={color} />
                </marker>
              ))}
            </defs>
            <g ref={view.canvasRef} data-graph-canvas="">
              {layout.bands.map((band) => (
                <BandBox key={band.name} band={band} />
              ))}
              {drawn.map((edge) => (
                <EdgeLine
                  key={edge.id}
                  edge={edge}
                  active={isActive(edge)}
                  dimmed={near !== null && !isActive(edge)}
                  // Bands can hold many edges of one predicate, so only the active ones are labelled there.
                  showLabel={layout.bands.length === 0 || isActive(edge)}
                  marker={`url(#arrow-${isActive(edge) ? 'active-' : ''}${uid})`}
                />
              ))}
              {layout.nodes.map((node) => (
                <NodeCard
                  key={node.id}
                  node={node}
                  selected={node.id === selected}
                  dimmed={near !== null && !near.has(node.id)}
                  onSelect={onSelect ? () => onSelect(node.data) : undefined}
                  onHover={setHovered}
                />
              ))}
            </g>
          </svg>
        )}
      </div>
    </div>
  )
}

interface DrawnNode {
  id: string
  width: number
  height: number
  band: string
  data: KnowledgeGraphNode
}

function layoutGraph(
  nodes: KnowledgeGraphNode[],
  edges: KnowledgeGraphEdge[],
  direction: LayoutDirection,
  bandOrder: string[] | undefined,
): { nodes: Positioned<DrawnNode>[]; edges: DrawnEdge[]; bands: Band[]; bounds: { width: number; height: number } } {
  const byId = new Map<string, DrawnNode>()
  for (const node of nodes) {
    if (byId.has(node.iri)) continue
    byId.set(node.iri, { id: node.iri, width: NODE_WIDTH, height: NODE_HEIGHT, band: node.layer ?? NO_BAND, data: node })
  }
  const links = edges
    .filter((edge) => byId.has(edge.source) && byId.has(edge.target))
    .map((edge, i) => ({
      ...edge,
      id: `${i}|${edge.source}|${edge.predicate}|${edge.target}`,
      labelWidth: Math.min(edge.predicate.length, PREDICATE_CHARS) * 6 + 8,
      labelHeight: 16,
    }))
  const items = [...byId.values()]
  if (items.length === 0) return { nodes: [], edges: [], bands: [], bounds: { width: 0, height: 0 } }

  if (bandOrder) {
    const result = computeBandLayout(items, links, bandOrder)
    return {
      ...result,
      edges: result.edges.map((edge) => ({ ...edge, path: curvePath(edge.points), label: middle(edge.points) })),
    }
  }
  const result = computeLayout(items, links, { direction, density: 'compact' })
  return {
    nodes: result.nodes,
    edges: result.edges.map((edge) => ({ ...edge, path: edgePath(edge.points, 10), label: edge.label ?? middle(edge.points) })),
    bands: [],
    bounds: result.bounds,
  }
}

function middle(points: Point[]): Point | undefined {
  if (points.length !== 4) return points[Math.floor(points.length / 2)]
  // The curve's point at t = 0.5.
  const [a, b, c, d] = points
  return { x: (a.x + 3 * b.x + 3 * c.x + d.x) / 8, y: (a.y + 3 * b.y + 3 * c.y + d.y) / 8 }
}

function BandBox({ band }: { band: Band }) {
  const color = graphColor(band.name, undefined)
  return (
    <g pointerEvents="none">
      <rect
        x={band.x}
        y={band.y}
        width={band.width}
        height={band.height}
        rx={16}
        fill={color.tint}
        fillOpacity={0.6}
        stroke={color.fill}
        strokeOpacity={0.35}
        strokeWidth={1.2}
        strokeDasharray="6 5"
      />
      <text x={band.x + 18} y={band.y + 26} fontSize="13" fontWeight="700" letterSpacing="0.6" fill={color.fill}>
        {band.name.toUpperCase()}
      </text>
    </g>
  )
}

function EdgeLine({
  edge,
  active,
  dimmed,
  showLabel,
  marker,
}: {
  edge: DrawnEdge
  active: boolean
  dimmed: boolean
  showLabel: boolean
  marker: string
}) {
  const color = active ? EDGE_ACTIVE_COLOR : EDGE_COLOR
  return (
    <g opacity={dimmed ? 0.18 : active ? 1 : 0.7} pointerEvents="none">
      <path
        d={edge.path}
        fill="none"
        stroke={color}
        strokeWidth={active ? 2 : 1.3}
        strokeDasharray={edge.tentative ? '6 4' : undefined}
        markerEnd={marker}
      />
      {showLabel && edge.label && (
        <text
          x={edge.label.x}
          y={edge.label.y + 3}
          textAnchor="middle"
          fontSize="10"
          fontWeight="600"
          fill={color}
          stroke="#ffffff"
          strokeWidth="4"
          paintOrder="stroke"
        >
          {truncate(edge.predicate, PREDICATE_CHARS)}
        </text>
      )}
    </g>
  )
}

function NodeCard({
  node,
  selected,
  dimmed,
  onSelect,
  onHover,
}: {
  node: Positioned<DrawnNode>
  selected: boolean
  dimmed: boolean
  onSelect?: () => void
  onHover: (id: string | undefined) => void
}) {
  const { data, width, height } = node
  const color = graphColor(data.layer, data.type)
  return (
    <g
      transform={`translate(${node.x - width / 2},${node.y - height / 2})`}
      opacity={dimmed ? 0.35 : 1}
      onClick={onSelect}
      onMouseEnter={() => onHover(node.id)}
      onMouseLeave={() => onHover(undefined)}
      onFocus={() => onHover(node.id)}
      onBlur={() => onHover(undefined)}
      onKeyDown={(event) => {
        if (onSelect && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault()
          onSelect()
        }
      }}
      role={onSelect ? 'button' : undefined}
      tabIndex={onSelect ? 0 : undefined}
      aria-label={[data.type, data.label].filter(Boolean).join(' ')}
      style={{ cursor: onSelect ? 'pointer' : 'default' }}
    >
      <title>{data.iri}</title>
      <rect
        width={width}
        height={height}
        rx={11}
        fill="#ffffff"
        stroke={selected ? colors.primary : color.fill}
        strokeWidth={selected ? 2.8 : 1.4}
        strokeDasharray={data.tentative ? '6 4' : undefined}
      />
      <rect x={0} y={9} width={6} height={height - 18} rx={3} fill={color.fill} />
      {data.type && (
        <text x={15} y={18} fontSize="9" fontWeight="700" letterSpacing="0.5" fill={color.fill}>
          {truncate(data.type, 32).toUpperCase()}
        </text>
      )}
      <text x={15} y={data.type ? 36 : 30} fontSize="12.5" fontWeight="650" fill={colors.textPrimary}>
        {truncate(data.label, LABEL_CHARS)}
      </text>
      {data.caption && (
        <text x={15} y={data.type ? 54 : 50} fontSize="10" fill={colors.textMuted}>
          {truncate(data.caption, 34)}
        </text>
      )}
    </g>
  )
}
