import { useMemo, type CSSProperties, type ReactNode } from 'react'
import { colors } from '@/theme'
import { parseMarkdown, summaryOf, type Block, type Inline } from './markdown'

const codeStyle: CSSProperties = {
  fontFamily: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
  fontSize: '0.92em',
  background: colors.surfaceMuted,
  border: `1px solid ${colors.borderLight}`,
  borderRadius: 4,
  padding: '0 4px',
  overflowWrap: 'anywhere',
}

const blockCodeStyle: CSSProperties = {
  fontFamily: codeStyle.fontFamily,
  fontSize: 12,
  background: colors.surfaceInset,
  border: `1px solid ${colors.borderLight}`,
  borderRadius: 6,
  padding: '8px 10px',
  margin: '0 0 8px',
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
  color: colors.textPrimary,
}

const cellStyle: CSSProperties = {
  border: `1px solid ${colors.border}`,
  padding: '4px 8px',
  textAlign: 'left',
  verticalAlign: 'top',
}

function renderInline(nodes: Inline[]): ReactNode[] {
  return nodes.map((node, i) => {
    switch (node.kind) {
      case 'text':
        return node.text
      case 'code':
        return (
          <code key={i} style={codeStyle}>
            {node.text}
          </code>
        )
      case 'strong':
        return (
          <strong key={i} style={{ color: colors.textPrimary, fontWeight: 600 }}>
            {renderInline(node.children)}
          </strong>
        )
      case 'em':
        return <em key={i}>{renderInline(node.children)}</em>
      case 'link':
        return (
          <a key={i} href={node.href} target="_blank" rel="noopener noreferrer nofollow">
            {renderInline(node.children)}
          </a>
        )
    }
  })
}

const HEADING_SIZES = [18, 16, 15, 14, 13, 13]

function renderBlocks(blocks: Block[], tight = false): ReactNode[] {
  const gap = tight ? 0 : 8
  return blocks.map((block, i) => {
    switch (block.kind) {
      case 'heading': {
        const Tag = `h${block.level}` as 'h1'
        return (
          <Tag
            key={i}
            style={{ fontSize: HEADING_SIZES[block.level - 1], fontWeight: 600, color: colors.textPrimary, margin: '12px 0 6px' }}
          >
            {renderInline(block.content)}
          </Tag>
        )
      }
      case 'paragraph':
        return (
          <p key={i} style={{ margin: `0 0 ${gap}px` }}>
            {renderInline(block.content)}
          </p>
        )
      case 'code':
        return (
          <pre key={i} style={blockCodeStyle} data-lang={block.lang || undefined}>
            <code>{block.text}</code>
          </pre>
        )
      case 'list': {
        const items = block.items.map((item, j) => <li key={j}>{renderBlocks(item, true)}</li>)
        const style: CSSProperties = { margin: `0 0 ${gap}px`, paddingLeft: 20 }
        return block.ordered ? (
          <ol key={i} start={block.start} style={style}>
            {items}
          </ol>
        ) : (
          <ul key={i} style={style}>
            {items}
          </ul>
        )
      }
      case 'quote':
        return (
          <blockquote
            key={i}
            style={{ margin: `0 0 ${gap}px`, padding: '0 0 0 10px', borderLeft: `3px solid ${colors.border}` }}
          >
            {renderBlocks(block.blocks)}
          </blockquote>
        )
      case 'table':
        return (
          <div key={i} style={{ overflowX: 'auto', margin: `0 0 ${gap}px` }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  {block.header.map((cell, c) => (
                    <th key={c} style={{ ...cellStyle, background: colors.surfaceSubtle, fontWeight: 600 }}>
                      {renderInline(cell)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) => (
                      <td key={c} style={cellStyle}>
                        {renderInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      case 'rule':
        return <hr key={i} style={{ border: 0, borderTop: `1px solid ${colors.border}`, margin: '8px 0' }} />
    }
  })
}

/**
 * Renders an MCP description written in Markdown. `summary` shows only its first
 * paragraph, inline, for table cells; the full text is in the inspector.
 */
export default function MarkdownText({
  source,
  summary = false,
  style,
}: {
  source: string | null | undefined
  summary?: boolean
  style?: CSSProperties
}) {
  const blocks = useMemo(() => parseMarkdown(source ?? ''), [source])
  if (!source) return null
  if (summary) return <span style={style}>{renderInline(summaryOf(blocks))}</span>
  return <div style={{ lineHeight: 1.55, overflowWrap: 'anywhere', ...style }}>{renderBlocks(blocks)}</div>
}
