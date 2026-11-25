import React, { useState } from 'react'
import { Alert, Button, Empty, Input, InputNumber, List, Segmented, Space, Switch, Tag, Tooltip, message } from 'antd'
import { DislikeOutlined, LikeOutlined } from '@ant-design/icons'
import {
  knowledgeErrorMessage,
  useDocuments,
  useRecommendFromKnowledge,
  useSearchKnowledge,
  useSendRecommendationFeedback,
  type Knowledge,
  type RecommendationItem,
} from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { Muted, NeutralTag } from '../shared'
import { hasVectors } from './bases'
import IriFilter from './IriFilter'
import { isIri, localName } from './sparql'

interface SearchTabProps {
  base: Knowledge
  /** Opens an entity in the graph explorer. */
  onOpenEntity?: (iri: string) => void
}

/** Tries the knowledge-base's search and its recommendations. */
export default function SearchTab({ base, onOpenEntity }: SearchTabProps) {
  const { can } = useKnowledge()
  const modes = [
    ...(can('searchKnowledge') && hasVectors(base) ? [{ label: 'Search', value: 'search' }] : []),
    ...(can('recommendFromKnowledge') ? [{ label: 'Recommend', value: 'recommend' }] : []),
  ]
  const [mode, setMode] = useState(modes[0]?.value)
  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      {!hasVectors(base) && <Alert type="info" showIcon message="This knowledge-base has no vector store, so its documents can't be searched." />}
      {modes.length > 1 && <Segmented value={mode} onChange={(value) => setMode(value as string)} options={modes} />}
      {mode === 'search' && <Search base={base} onOpenEntity={onOpenEntity} />}
      {mode === 'recommend' && <Recommend base={base} onOpenEntity={onOpenEntity} />}
    </Space>
  )
}

function EntityTag({ iri, onOpen }: { iri: string; onOpen?: (iri: string) => void }) {
  if (!onOpen || !isIri(iri)) return <NeutralTag title={iri}>{localName(iri)}</NeutralTag>
  return (
    <Tooltip title={iri}>
      <Button size="small" type="link" style={{ padding: 0 }} onClick={() => onOpen(iri)}>{localName(iri)}</Button>
    </Tooltip>
  )
}

function Search({ base, onOpenEntity }: SearchTabProps) {
  const { instance, can } = useKnowledge()
  const search = useSearchKnowledge(instance)
  const documents = useDocuments(instance, base.id, { enabled: can('listKnowledgeDocument') })
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(base.retrieval?.topK ?? 8)
  const [threshold, setThreshold] = useState(base.retrieval?.scoreThreshold ?? 0)
  const [hybrid, setHybrid] = useState(base.retrieval?.hybrid ?? true)
  const [iris, setIris] = useState<string[]>([])

  function run() {
    if (!query.trim()) return
    search.mutate({
      id: base.id,
      request: { query: query.trim(), topK, scoreThreshold: threshold, hybrid, iriFilter: iris.length ? iris : undefined },
    })
  }
  const result = search.data
  return (
    <>
      <Input.Search
        aria-label="Search query"
        enterButton="Search"
        placeholder="What do you want to know?"
        value={query}
        loading={search.isPending}
        onChange={(e) => setQuery(e.target.value)}
        onSearch={run}
      />
      <Space wrap size={16}>
        <Labelled text="Hits"><InputNumber aria-label="Hits" min={1} max={100} precision={0} value={topK} onChange={(v) => setTopK(v ?? 8)} style={{ width: 80 }} /></Labelled>
        <Labelled text="Minimum score">
          <InputNumber aria-label="Minimum score" min={0} max={1} step={0.05} value={threshold} onChange={(v) => setThreshold(v ?? 0)} style={{ width: 90 }} />
        </Labelled>
        <Labelled text="Hybrid"><Switch aria-label="Hybrid" checked={hybrid} onChange={setHybrid} /></Labelled>
      </Space>
      <IriFilter aria-label="IRI filter" value={iris} onChange={setIris} placeholder="Only chunks that mention these classes or entities (IRIs)" />
      {search.isError && <Alert type="error" showIcon message={knowledgeErrorMessage(search.error)} />}
      {result && result.mode === 'sparse' && (
        <Alert type="warning" showIcon message="No embedding model is available: the search used keywords only." />
      )}
      {result && !search.isError && (
        <List
          bordered
          loading={search.isPending}
          dataSource={result.hits}
          locale={{ emptyText: 'Nothing matches.' }}
          renderItem={(hit) => (
            <List.Item key={hit.chunkId}>
              <div style={{ width: '100%' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                  <Muted>
                    {hit.documentId ? documents.data?.find((d) => d.id === hit.documentId)?.name ?? hit.documentId : hit.chunkId}
                  </Muted>
                  <Tag color="blue" style={{ marginInlineEnd: 0 }}>{hit.score.toFixed(3)}</Tag>
                </div>
                <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: '4px 0' }}>{hit.text}</div>
                {(hit.entityIri?.length ?? 0) > 0 && (
                  <Space size={8} wrap>{hit.entityIri!.map((iri) => <EntityTag key={iri} iri={iri} onOpen={onOpenEntity} />)}</Space>
                )}
              </div>
            </List.Item>
          )}
        />
      )}
      {!result && !search.isError && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Search the documents of this knowledge-base" />}
    </>
  )
}

function Recommend({ base, onOpenEntity }: SearchTabProps) {
  const { instance, can } = useKnowledge()
  const recommend = useRecommendFromKnowledge(instance)
  const feedback = useSendRecommendationFeedback(instance)
  const [context, setContext] = useState('')
  const [iris, setIris] = useState<string[]>([])
  const [topK, setTopK] = useState(8)
  // The items of this answer that got feedback.
  const [rated, setRated] = useState<Record<string, 'success' | 'failure'>>({})
  const result = recommend.data

  function run() {
    setRated({})
    recommend.mutate({ id: base.id, request: { context: context.trim() || undefined, iri: iris.length ? iris : undefined, topK } })
  }
  function rate(item: RecommendationItem, outcome: 'success' | 'failure') {
    feedback.mutate(
      { id: base.id, feedback: { recommendationId: result!.recommendationId!, itemId: item.itemId, used: true, outcome } },
      {
        onSuccess: () => setRated((current) => ({ ...current, [item.itemId]: outcome })),
        onError: (error) => message.error(knowledgeErrorMessage(error), 6),
      },
    )
  }
  const signals = (item: RecommendationItem) =>
    [
      item.signals?.successRate !== undefined && `success ${(item.signals.successRate * 100).toFixed(0)}%`,
      item.signals?.applicability !== undefined && `applicability ${item.signals.applicability.toFixed(2)}`,
      item.signals?.usage !== undefined && `used ${item.signals.usage}×`,
    ].filter(Boolean).join(' · ')

  return (
    <>
      <Input.TextArea
        aria-label="Context"
        value={context}
        onChange={(e) => setContext(e.target.value)}
        placeholder="Describe the situation"
        autoSize={{ minRows: 2, maxRows: 5 }}
      />
      <IriFilter aria-label="Entities" value={iris} onChange={setIris} placeholder="Entities or classes it is about (IRIs)" />
      <Space size={16}>
        <Labelled text="Items"><InputNumber aria-label="Items" min={1} max={100} precision={0} value={topK} onChange={(v) => setTopK(v ?? 8)} style={{ width: 80 }} /></Labelled>
        <Button type="primary" loading={recommend.isPending} disabled={!context.trim() && iris.length === 0} onClick={run}>Recommend</Button>
      </Space>
      {recommend.isError && <Alert type="error" showIcon message={knowledgeErrorMessage(recommend.error)} />}
      {result && !recommend.isError && (
        <List
          bordered
          loading={recommend.isPending}
          dataSource={result.items ?? []}
          locale={{ emptyText: 'Nothing to recommend.' }}
          renderItem={(item) => (
            <List.Item
              key={item.itemId}
              actions={
                can('createRecommendationFeedback') && result.recommendationId
                  ? [
                      <Tooltip key="up" title="It helped">
                        <Button size="small" aria-label={`Helped: ${item.label ?? item.itemId}`} type={rated[item.itemId] === 'success' ? 'primary' : 'default'} disabled={!!rated[item.itemId]} icon={<LikeOutlined />} onClick={() => rate(item, 'success')} />
                      </Tooltip>,
                      <Tooltip key="down" title="It didn't help">
                        <Button size="small" aria-label={`Did not help: ${item.label ?? item.itemId}`} type={rated[item.itemId] === 'failure' ? 'primary' : 'default'} disabled={!!rated[item.itemId]} icon={<DislikeOutlined />} onClick={() => rate(item, 'failure')} />
                      </Tooltip>,
                    ]
                  : undefined
              }
            >
              <div style={{ minWidth: 0 }}>
                <Space size={8} wrap>
                  <NeutralTag>{item.kind}</NeutralTag>
                  {item.iri ? <EntityTag iri={item.iri} onOpen={onOpenEntity} /> : null}
                  <Tag color="blue">{item.score.toFixed(3)}</Tag>
                </Space>
                <div style={{ wordBreak: 'break-word' }}>{item.label ?? item.itemId}</div>
                {signals(item) && <div style={{ fontSize: 12, color: colors.textMuted }}>{signals(item)}</div>}
              </div>
            </List.Item>
          )}
        />
      )}
    </>
  )
}

function Labelled({ text, children }: { text: string; children: React.ReactNode }) {
  return (
    <Space size={6}>
      <span style={{ fontSize: 12, color: colors.textSecondary }}>{text}</span>
      {children}
    </Space>
  )
}
