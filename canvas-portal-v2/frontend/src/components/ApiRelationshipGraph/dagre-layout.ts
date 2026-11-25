// The Kubernetes side of the graph: node sizes per type, and edges with their relation. The generic layout and
// edge path are in components/graph/layout.ts.

import type { Point } from '@/components/graph/layout'
import type { NodeType, RelationType } from './style'

const NODE_DIMS: Record<NodeType, { width: number; height: number }> = {
  component:          { width: 290, height: 108 },
  exposedApi:         { width: 222, height: 66 },
  dependentApi:       { width: 222, height: 66 },
  virtualService:     { width: 208, height: 62 },
  httpRoute:          { width: 208, height: 62 },
  apisixRoute:        { width: 208, height: 62 },
  kongIngress:        { width: 208, height: 62 },
  gateway:            { width: 208, height: 62 },
  kongPlugin:         { width: 190, height: 56 },
  apisixPluginConfig: { width: 190, height: 56 },
}

export function nodeDimensions(t: NodeType): { width: number; height: number } {
  return NODE_DIMS[t] ?? { width: 180, height: 60 }
}

export interface PositionedEdge {
  id: string
  source: string
  target: string
  relation: RelationType
  points: Point[]   // polyline waypoints (includes endpoints)
}
