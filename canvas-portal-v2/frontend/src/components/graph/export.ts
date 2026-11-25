import type { Bounds } from './layout'

/** The graph as an SVG file. resetView drops the current pan and zoom, so the file shows the whole graph. */
export function svgSource(svgEl: SVGSVGElement, bounds: Bounds, { resetView = false } = {}): string {
  const clone = svgEl.cloneNode(true) as SVGSVGElement
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  // Bake a sensible width/height so the file opens at a useful size in Inkscape/Figma.
  clone.setAttribute('width', String(bounds.width))
  clone.setAttribute('height', String(bounds.height))
  clone.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`)
  if (resetView) clone.querySelector('[data-graph-canvas]')?.removeAttribute('transform')
  return '<?xml version="1.0" standalone="no"?>\n' + new XMLSerializer().serializeToString(clone)
}

export function escapeXmlText(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function escapeXmlAttr(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function downloadBlob(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

export function timestamp(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
}
