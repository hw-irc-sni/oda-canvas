import React, { useLayoutEffect, useState } from 'react'
import { colors } from '@/theme'

/** Marks a dot the flow line passes through; the value is its position in the flow. */
export const FLOW_STEP_ATTR = 'data-flow-step'

interface Point {
  x: number
  y: number
}

/** A smooth curve through the points, bowing left and right in turn so it reads as one flow. */
function curve(points: Point[]): string {
  if (points.length < 2) return ''
  let d = `M ${points[0].x} ${points[0].y}`
  for (let i = 1; i < points.length; i++) {
    const p = points[i - 1]
    const q = points[i]
    const dy = (q.y - p.y) / 2
    const bow = (i % 2 ? 1 : -1) * Math.min(60, 12 + Math.abs(q.y - p.y) / 4)
    d += ` C ${p.x + bow} ${p.y + dy}, ${q.x + bow} ${q.y - dy}, ${q.x} ${q.y}`
  }
  return d
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

interface FlowLineProps {
  /** The element holding the dots; the line is drawn over it. */
  container: React.RefObject<HTMLElement>
  /** Changes whenever the dots may have moved or changed. */
  flowKey: string
}

/** Draws an animated green line through the flow dots inside the container. */
export default function FlowLine({ container, flowKey }: FlowLineProps) {
  const [path, setPath] = useState('')
  const [size, setSize] = useState({ width: 0, height: 0 })

  useLayoutEffect(() => {
    const root = container.current
    if (!root) return
    let frame = 0

    const measure = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const box = root.getBoundingClientRect()
        const points = [...root.querySelectorAll<HTMLElement>(`[${FLOW_STEP_ATTR}]`)]
          .sort((a, b) => Number(a.getAttribute(FLOW_STEP_ATTR)) - Number(b.getAttribute(FLOW_STEP_ATTR)))
          .map((dot) => {
            const r = dot.getBoundingClientRect()
            return { x: r.left + r.width / 2 - box.left, y: r.top + r.height / 2 - box.top }
          })
        setSize({ width: box.width, height: box.height })
        setPath(curve(points))
      })
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(root)
    // The tables scroll sideways on narrow screens, which moves the dots.
    root.addEventListener('scroll', measure, true)
    window.addEventListener('resize', measure)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      root.removeEventListener('scroll', measure, true)
      window.removeEventListener('resize', measure)
    }
  }, [container, flowKey])

  if (!path) return null
  const animate = !prefersReducedMotion()
  const green = colors.statusLive

  return (
    <svg
      aria-hidden
      width={size.width}
      height={size.height}
      style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 1, overflow: 'visible' }}
    >
      <path d={path} fill="none" stroke={green} strokeOpacity={0.25} strokeWidth={6} strokeLinecap="round" />
      <path d={path} fill="none" stroke={green} strokeWidth={2} strokeLinecap="round" strokeDasharray="8 7">
        {animate && <animate attributeName="stroke-dashoffset" from="30" to="0" dur="0.9s" repeatCount="indefinite" />}
      </path>
      {animate &&
        [0, 1.5].map((begin) => (
          <circle key={begin} r={4} fill={green}>
            <animateMotion path={path} dur="3s" begin={`${begin}s`} repeatCount="indefinite" rotate="auto" />
          </circle>
        ))}
    </svg>
  )
}
