import React, { useState } from 'react'
import { Alert, Button, Card, Col, Descriptions, Popconfirm, Row, Space, Statistic, Tag, message } from 'antd'
import { DeleteOutlined, EditOutlined } from '@ant-design/icons'
import {
  knowledgeErrorMessage,
  usePatchKnowledgeBase,
  type Knowledge,
  type LifecycleStatus,
  type ModelRoute,
} from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import { Muted, NeutralTag, formatDate } from '../shared'
import { describeSource, hasVectors, mayEdit } from './bases'

const ROUTE_LABELS: Record<string, string> = {
  modelGatewayService: 'Model Gateway Service',
  agentgateway: 'agentgateway',
  openaiCompatible: 'OpenAI-compatible API',
}

function RouteTag({ route }: { route: ModelRoute | null | undefined }) {
  if (!route) return <Muted>unknown</Muted>
  if (route.status === 'up') return <Tag color="green">{(route.route && (ROUTE_LABELS[route.route] ?? route.route)) || 'up'}</Tag>
  if (route.status === 'down') return <Tag color="red">none is available</Tag>
  if (route.status === 'not-configured') return <NeutralTag>not configured</NeutralTag>
  return <NeutralTag>not resolved yet</NeutralTag>
}

interface OverviewTabProps {
  base: Knowledge
  onEdit: () => void
  onDelete: () => void
}

export default function OverviewTab({ base, onEdit, onDelete }: OverviewTabProps) {
  const access = useKnowledge()
  const owner = base.relatedParty?.find((party) => party.role === 'owner')?.partyOrPartyRole
  return (
    <Space direction="vertical" size={18} style={{ width: '100%' }}>
      {base.description && <div>{base.description}</div>}
      <Row gutter={12}>
        <Col span={8}><Card size="small"><Statistic title="Triples" value={base.statistics?.triples ?? '—'} /></Card></Col>
        <Col span={8}><Card size="small"><Statistic title="Documents" value={base.statistics?.documents ?? '—'} /></Card></Col>
        <Col span={8}>
          <Card size="small"><Statistic title="Chunks" value={hasVectors(base) ? base.statistics?.chunks ?? '—' : 'no vectors'} /></Card>
        </Col>
      </Row>
      <Lifecycle base={base} />
      <Descriptions column={1} size="small" bordered labelStyle={{ width: 160 }}>
        <Descriptions.Item label="Id"><code>{base.id}</code></Descriptions.Item>
        <Descriptions.Item label="Version">{base.version || '—'}</Descriptions.Item>
        <Descriptions.Item label="Categories">
          {base.knowledgeCategory?.length ? (
            <Space size={4} wrap>{base.knowledgeCategory.map((c) => <NeutralTag key={c}>{c}</NeutralTag>)}</Space>
          ) : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="Store">{base.storeKind ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="Conforms to">{base.conformsTo?.map((ref) => ref.name ?? ref.id).join(', ') || '—'}</Descriptions.Item>
        <Descriptions.Item label="Sources">
          {base.source?.length ? base.source.map((s, i) => <div key={i}>{describeSource(s)}</div>) : '—'}
        </Descriptions.Item>
        {hasVectors(base) && (
          <Descriptions.Item label="Retrieval defaults">
            {`${base.retrieval?.topK ?? 8} hits, minimum score ${base.retrieval?.scoreThreshold ?? 0}, ${base.retrieval?.hybrid === false ? 'dense search' : 'hybrid search'}`}
          </Descriptions.Item>
        )}
        <Descriptions.Item label="Active model route"><RouteTag route={access.info.modelRoute} /></Descriptions.Item>
        <Descriptions.Item label="Owner">{owner?.name || owner?.id || '—'}</Descriptions.Item>
        <Descriptions.Item label="Last update">{formatDate(base.lastUpdate)}</Descriptions.Item>
      </Descriptions>
      <Space size={8}>
        {mayEdit(access, base) && <Button icon={<EditOutlined />} onClick={onEdit}>Edit</Button>}
        {access.can('deleteKnowledge') && <Button danger icon={<DeleteOutlined />} onClick={onDelete}>Delete</Button>}
      </Space>
    </Space>
  )
}

const MOVES: Record<LifecycleStatus, { label: string; done: string; confirm?: string; danger?: boolean }> = {
  proposed: { label: 'Propose', done: 'Proposed for review' },
  inReview: { label: 'Start the review', done: 'The review started' },
  agreed: { label: 'Agree', done: 'Agreed', confirm: 'An approver can then publish it.' },
  rejected: { label: 'Reject', done: 'Rejected', confirm: 'An editor can then revise it.', danger: true },
  draft: { label: 'Revise', done: 'Back to draft' },
  published: { label: 'Publish', done: 'Published', confirm: 'It becomes a published knowledge-base.' },
  deprecated: {
    label: 'Deprecate',
    done: 'Deprecated',
    confirm: "Its content can't be changed any more. Deprecation can't be undone.",
    danger: true,
  },
}

/** The lifecycle changes the user may make from the current state. */
function Lifecycle({ base }: { base: Knowledge }) {
  const access = useKnowledge()
  const patch = usePatchKnowledgeBase(access.instance)
  const [failure, setFailure] = useState<string>()
  const next = access.can('patchKnowledge') ? access.nextStates('knowledge', base.lifecycleStatus) : []
  if (next.length === 0 && !failure) return null
  const move = (to: LifecycleStatus) => {
    setFailure(undefined)
    patch.mutate(
      { id: base.id, patch: { lifecycleStatus: to } },
      {
        onSuccess: () => message.success(MOVES[to].done),
        onError: (error) => setFailure(knowledgeErrorMessage(error)),
      },
    )
  }
  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Space size={8} wrap>
        {next.map((to) => {
          const { label, confirm, danger } = MOVES[to]
          const button = (onClick?: () => void) => (
            <Button key={to} type="primary" danger={danger} loading={patch.isPending} onClick={onClick}>{label}</Button>
          )
          return confirm ? (
            <Popconfirm key={to} title={`${label}?`} description={confirm} okText={label} onConfirm={() => move(to)}>
              {button()}
            </Popconfirm>
          ) : (
            button(() => move(to))
          )
        })}
      </Space>
      {failure && <Alert type="error" showIcon message={failure} />}
    </Space>
  )
}
