import type React from 'react'
import { colors } from '@/theme'

/** The block around a graph and its toolbar; in fullscreen it covers the page. */
export function graphContainerStyle(isFullscreen: boolean): React.CSSProperties {
  if (!isFullscreen) return {}
  return {
    position: 'fixed',
    top: 0,
    left: 0,
    width: '100vw',
    height: '100vh',
    zIndex: 1000,
    background: '#ffffff',
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    overflow: 'hidden',
  }
}

/** The graph's canvas. */
export function graphSurfaceStyle(isFullscreen: boolean, height: number): React.CSSProperties {
  return {
    position: 'relative',
    width: '100%',
    height: isFullscreen ? '100%' : height,
    flex: isFullscreen ? 1 : undefined,
    border: `1px solid ${colors.border}`,
    borderRadius: 14,
    overflow: 'hidden',
    background: colors.surfaceSubtle,
    boxShadow: 'inset 0 1px 2px rgba(15,23,42,0.03)',
  }
}

export const emptySurfaceStyle: React.CSSProperties = {
  height: '100%',
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'center',
}

export function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 3)}...` : value
}
