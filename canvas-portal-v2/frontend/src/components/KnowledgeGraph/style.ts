export interface GraphColor {
  fill: string
  tint: string
}

const LAYER_COLORS: Record<string, GraphColor> = {
  Foundational: { fill: '#1d4ed8', tint: '#eff6ff' },
  Telecom: { fill: '#059669', tint: '#ecfdf5' },
  Implementation: { fill: '#7c3aed', tint: '#f5f3ff' },
}

const TYPE_COLORS: GraphColor[] = [
  { fill: '#0369a1', tint: '#f0f9ff' },
  { fill: '#15803d', tint: '#f0fdf4' },
  { fill: '#c2410c', tint: '#fff7ed' },
  { fill: '#6d28d9', tint: '#f5f3ff' },
  { fill: '#be185d', tint: '#fdf2f8' },
  { fill: '#0f766e', tint: '#f0fdfa' },
  { fill: '#a16207', tint: '#fefce8' },
  { fill: '#4338ca', tint: '#eef2ff' },
]

const UNTYPED: GraphColor = { fill: '#64748b', tint: '#f8fafc' }

export const EDGE_COLOR = '#64748b'
export const EDGE_ACTIVE_COLOR = '#0072CE'

/** The same type always has the same colour, whatever else is in the graph. */
function typeColor(type: string): GraphColor {
  let hash = 0
  for (let i = 0; i < type.length; i++) hash = (hash * 31 + type.charCodeAt(i)) >>> 0
  return TYPE_COLORS[hash % TYPE_COLORS.length]
}

export function graphColor(layer: string | undefined, type: string | undefined): GraphColor {
  if (layer && LAYER_COLORS[layer]) return LAYER_COLORS[layer]
  return type ? typeColor(type) : UNTYPED
}
