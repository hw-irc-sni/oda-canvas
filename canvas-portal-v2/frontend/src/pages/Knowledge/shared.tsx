import React from 'react'
import { Alert, Button, Tag, Typography } from 'antd'
import { CopyOutlined, DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  knowledgeErrorMessage,
  saveBlob,
  type LifecycleStatus,
  type LinkStatus,
  type OntologyLayer,
} from '@/api/knowledge'
import { colors } from '@/theme'

export const LIFECYCLE_STATUSES: LifecycleStatus[] = [
  'draft',
  'proposed',
  'inReview',
  'agreed',
  'rejected',
  'published',
  'deprecated',
]

const STATUS: Record<LifecycleStatus, { label: string; color?: string }> = {
  draft: { label: 'Draft' },
  proposed: { label: 'Proposed', color: 'blue' },
  inReview: { label: 'In review', color: 'gold' },
  agreed: { label: 'Agreed', color: 'cyan' },
  rejected: { label: 'Rejected', color: 'red' },
  published: { label: 'Published', color: 'green' },
  deprecated: { label: 'Deprecated', color: 'volcano' },
}

export function statusLabel(status: LifecycleStatus | undefined): string {
  return status ? STATUS[status]?.label ?? status : 'Unknown'
}

export function StatusTag({ status }: { status?: LifecycleStatus }) {
  if (!status) return <Muted>—</Muted>
  const color = STATUS[status]?.color
  if (!color) return <NeutralTag>{statusLabel(status)}</NeutralTag>
  return <Tag color={color} style={{ marginInlineEnd: 0 }}>{statusLabel(status)}</Tag>
}

/** A tag that means nothing good or bad: the theme's default tag is red. */
export function NeutralTag({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <Tag
      title={title}
      style={{ marginInlineEnd: 0, background: colors.surfaceMuted, borderColor: colors.border, color: colors.textSecondary }}
    >
      {children}
    </Tag>
  )
}

/** From the specialised layer to the general one, the direction of the imports. */
export const LAYERS: OntologyLayer[] = ['Implementation', 'Telecom', 'Foundational']

const LAYER_TAGS: Record<OntologyLayer, string> = { Foundational: 'blue', Telecom: 'green', Implementation: 'purple' }

export function LayerTag({ layer }: { layer?: OntologyLayer }) {
  if (!layer) return <Muted>—</Muted>
  return <Tag color={LAYER_TAGS[layer]} style={{ marginInlineEnd: 0 }}>{layer}</Tag>
}

export function LinkStatusTag({ status }: { status?: LinkStatus }) {
  if (!status) return <Muted>—</Muted>
  return <Tag color={status === 'confirmed' ? 'green' : 'orange'} style={{ marginInlineEnd: 0 }}>{status}</Tag>
}

export function Muted({ children }: { children: React.ReactNode }) {
  return <span style={{ color: colors.textMuted }}>{children}</span>
}

export function formatDate(value: string | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

/** A failed read, with a retry. */
export function QueryError({ error, what, onRetry }: { error: unknown; what: string; onRetry?: () => void }) {
  return (
    <Alert
      type="error"
      showIcon
      message={`Failed to load ${what}`}
      description={knowledgeErrorMessage(error)}
      action={onRetry && <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>Retry</Button>}
    />
  )
}

// Browsers struggle with very long text blocks; the download is always complete.
const MAX_SHOWN_CHARS = 200_000

interface CodeBlockProps {
  text: string
  filename?: string
  maxHeight?: number
}

/** Monospace text (Turtle, JSON) with copy and, with a filename, download. */
export function CodeBlock({ text, filename, maxHeight = 420 }: CodeBlockProps) {
  const cut = text.length > MAX_SHOWN_CHARS
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginBottom: 6 }}>
        <Typography.Text copyable={{ text, icon: [<CopyOutlined key="c" />] }} style={{ fontSize: 12 }} />
        {filename && (
          <Button size="small" icon={<DownloadOutlined />} onClick={() => saveBlob(new Blob([text]), filename)}>
            Download
          </Button>
        )}
      </div>
      <pre
        style={{
          margin: 0,
          maxHeight,
          overflow: 'auto',
          padding: 14,
          borderRadius: 10,
          background: colors.codeBg,
          color: colors.codeText,
          fontSize: 12,
          lineHeight: 1.5,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        }}
      >
        {cut ? text.slice(0, MAX_SHOWN_CHARS) : text}
      </pre>
      {cut && <Muted>Only the beginning is shown. Download it for the complete text.</Muted>}
    </div>
  )
}
