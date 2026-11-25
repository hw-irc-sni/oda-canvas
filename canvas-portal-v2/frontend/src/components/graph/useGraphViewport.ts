import { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'
import type { Bounds } from './layout'

export interface GraphViewportOptions {
  /** Whether the SVG is mounted: an empty graph shows an empty state instead of it. */
  hasNodes: boolean
  bounds: Bounds
  /** Changes when the graph's content does: the view is then fitted again. */
  layoutKey: string
  /** The canvas height outside fullscreen, which is also its minimum. */
  height: number
  minWidth: number
  padding: number
  minZoom: number
  maxZoom: number
  maxFitScale: number
}

/** Pan, wheel zoom, zoom buttons, fit to screen and fullscreen of an SVG graph. */
export function useGraphViewport({
  hasNodes,
  bounds,
  layoutKey,
  height,
  minWidth,
  padding,
  minZoom,
  maxZoom,
  maxFitScale,
}: GraphViewportOptions) {
  const wrapperRef = useRef<HTMLDivElement | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const canvasRef = useRef<SVGGElement | null>(null)
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null)
  const [viewportWidth, setViewportWidth] = useState(1160)
  const [viewportHeight, setViewportHeight] = useState(height)
  const [isFullscreen, setIsFullscreen] = useState(false)

  useEffect(() => {
    if (!wrapperRef.current) return
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (rect) {
        setViewportWidth(Math.max(minWidth, Math.floor(rect.width)))
        setViewportHeight(Math.max(height, Math.floor(rect.height)))
      }
    })
    observer.observe(wrapperRef.current)
    return () => observer.disconnect()
  }, [hasNodes, height, minWidth])

  // Exit fullscreen on Escape — standard browser pattern.
  useEffect(() => {
    if (!isFullscreen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsFullscreen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isFullscreen])

  // Attach d3-zoom whenever the SVG is mounted. Has to depend on hasNodes
  // because the SVG is conditionally rendered (empty state hides it), so refs
  // are null on the first render and a single-shot `useEffect(…, [])` would
  // never see them.
  useEffect(() => {
    if (!hasNodes) return
    const svgEl = svgRef.current
    const canvas = canvasRef.current
    if (!svgEl || !canvas) return
    const svg = d3.select(svgEl)
    const graphCanvas = d3.select(canvas)
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([minZoom, maxZoom])
      .filter((event) => {
        // Allow left-mouse-drag pan + wheel zoom; ignore right-click (so
        // browser context menu still works) and ignore drags that start
        // on a button so the toolbar still works inside the wrapper.
        if (event.type === 'mousedown' && event.button !== 0) return false
        const target = event.target as Element | null
        if (target?.closest('button')) return false
        return !event.ctrlKey && !event.metaKey
      })
      .on('zoom', (event) => graphCanvas.attr('transform', event.transform.toString()))
    zoomRef.current = zoom
    svg.call(zoom as never)
    // Set initial transform so subsequent fit-to-screen has something to compose with.
    svg.call((selection) => zoom.transform(selection as never, d3.zoomIdentity))
    return () => {
      svg.interrupt()
      svg.on('.zoom', null)
      zoomRef.current = null
    }
  }, [hasNodes, minZoom, maxZoom])

  useEffect(() => {
    fitToScreen()
  }, [layoutKey, bounds.width, bounds.height, viewportWidth, viewportHeight, isFullscreen])

  function zoomBy(factor: number) {
    const svgEl = svgRef.current
    const zoom = zoomRef.current
    if (!svgEl || !zoom) return
    d3.select(svgEl).transition().duration(180).call((selection) => zoom.scaleBy(selection as never, factor))
  }

  function fitToScreen() {
    const svgEl = svgRef.current
    const zoom = zoomRef.current
    if (!svgEl || !zoom || !hasNodes) return
    const scale = Math.min(
      (viewportWidth - padding * 2) / bounds.width,
      (viewportHeight - padding * 2) / bounds.height,
      maxFitScale,
    )
    const safeScale = Math.max(minZoom, scale)
    const translateX = (viewportWidth - bounds.width * safeScale) / 2
    const translateY = (viewportHeight - bounds.height * safeScale) / 2
    const transform = d3.zoomIdentity.translate(translateX, translateY).scale(safeScale)
    d3.select(svgEl).transition().duration(320).call((selection) => zoom.transform(selection as never, transform))
  }

  return {
    wrapperRef,
    svgRef,
    canvasRef,
    viewportWidth,
    viewportHeight,
    isFullscreen,
    toggleFullscreen: () => setIsFullscreen((v) => !v),
    zoomBy,
    fitToScreen,
  }
}
