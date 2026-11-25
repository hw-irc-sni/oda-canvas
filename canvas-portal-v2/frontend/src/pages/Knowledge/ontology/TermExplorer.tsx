import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Empty, Input, InputNumber, List, Space, Spin, Switch, Tag } from 'antd'
import { ArrowLeftOutlined, ArrowRightOutlined } from '@ant-design/icons'
import {
  knowledgeErrorMessage,
  knowledgeErrorStatus,
  useModules,
  useTermDescription,
  useTermSearch,
  type LayerChainEntry,
  type OntologyLayer,
  type TermDescription,
} from '@/api/knowledge'
import { graphColor } from '@/components/KnowledgeGraph/style'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { LayerTag, LinkStatusTag, Muted, NeutralTag, QueryError } from '../shared'
import { moduleName } from './modules'

/** An absolute IRI or a CURIE: something the describe operation accepts. */
export function looksLikeTerm(text: string): boolean {
  return /^[A-Za-z][A-Za-z0-9+.-]*:[^\s<>"{}|^`\\]+$/.test(text)
}

/** Search the ontology's terms, describe one and see its layer chain. */
export default function TermExplorer() {
  const { instance, can } = useKnowledge()
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(25)
  const [includeUnpublished, setIncludeUnpublished] = useState(false)
  // The described terms, oldest first: the last one is shown.
  const [trail, setTrail] = useState<string[]>([])
  const search = useTermSearch(instance, { q, limit, includeUnpublished })
  const modules = useModules(instance, { enabled: can('listKnowledgeSpecification') })
  const nameOf = (id: string | undefined) => {
    const module = modules.data?.find((m) => m.id === id)
    return module ? moduleName(module) : id
  }
  // The API gives a term its own fto:layer only; most classes just have their module's layer.
  const layerOf = (id: string | undefined) => modules.data?.find((m) => m.id === id)?.layer

  const term = trail[trail.length - 1]
  const describable = can('describeKnowledgeTerm')
  const open = (iri: string) => setTrail((current) => (current[current.length - 1] === iri ? current : [...current, iri]))
  const hits = search.data?.hits ?? []

  return (
    <div>
      <Space wrap size={12} style={{ marginBottom: 14 }}>
        <Input.Search
          allowClear
          enterButton="Search"
          placeholder="Label, definition, IRI or CURIE (such as Cell or net:Cell)"
          aria-label="Search terms"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onSearch={(value) => setQ(value.trim())}
          style={{ width: 430 }}
        />
        {describable && (
          <Button disabled={!looksLikeTerm(draft.trim())} onClick={() => open(draft.trim())}>
            Describe IRI or CURIE
          </Button>
        )}
        <Space size={6}>
          <span style={{ fontSize: 12, color: colors.textSecondary }}>Results</span>
          <InputNumber aria-label="Maximum results" min={1} max={1000} value={limit} onChange={(v) => setLimit(v ?? 25)} style={{ width: 80 }} />
        </Space>
        <Space size={6}>
          <Switch aria-label="Include unpublished modules" size="small" checked={includeUnpublished} onChange={setIncludeUnpublished} />
          <span style={{ fontSize: 12, color: colors.textSecondary }}>Include unpublished modules</span>
        </Space>
      </Space>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(300px, 2fr) minmax(360px, 3fr)', gap: 20, alignItems: 'start' }}>
        <div>
          {!q ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Search the terms of the ontology modules" />
          ) : search.isLoading ? (
            <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>
          ) : search.isError ? (
            <QueryError error={search.error} what="the terms" onRetry={() => search.refetch()} />
          ) : (
            <List
              size="small"
              loading={search.isFetching}
              dataSource={hits}
              locale={{
                emptyText: includeUnpublished
                  ? 'No term matches'
                  : 'No published term matches. Unpublished modules can be included with the switch above.',
              }}
              pagination={hits.length > 15 ? { pageSize: 15, size: 'small' } : false}
              renderItem={(hit) => (
                <List.Item
                  style={{
                    cursor: describable && hit.iri ? 'pointer' : 'default',
                    background: hit.iri === term ? colors.surfaceSelected : undefined,
                    paddingInline: 10,
                    borderRadius: 8,
                  }}
                  onClick={() => describable && hit.iri && open(hit.iri)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && describable && hit.iri) open(hit.iri)
                  }}
                  tabIndex={describable ? 0 : undefined}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{hit.text ?? hit.curie ?? hit.iri}</div>
                    <div style={{ fontSize: 11, color: colors.textMuted, wordBreak: 'break-all' }}>
                      {hit.curie ?? hit.iri} · {nameOf(hit.module) ?? 'unknown module'}
                      {hit.via && ` · matched by ${hit.via}`}
                    </div>
                  </div>
                  <LayerTag layer={hit.layer} />
                </List.Item>
              )}
            />
          )}
        </div>

        <div style={{ border: `1px solid ${colors.border}`, borderRadius: 12, padding: 16, minHeight: 160 }}>
          {term ? (
            <TermPanel
              key={term}
              iri={term}
              includeUnpublished={includeUnpublished}
              nameOf={nameOf}
              layerOf={layerOf}
              onOpen={open}
              onBack={trail.length > 1 ? () => setTrail((current) => current.slice(0, -1)) : undefined}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Select a term to describe it" />
          )}
        </div>
      </div>
    </div>
  )
}

interface TermPanelProps {
  iri: string
  includeUnpublished: boolean
  nameOf: (id: string | undefined) => string | undefined
  layerOf: (id: string | undefined) => OntologyLayer | undefined
  onOpen: (iri: string) => void
  onBack?: () => void
}

function TermPanel({ iri, includeUnpublished, nameOf, layerOf, onOpen, onBack }: TermPanelProps) {
  const { instance } = useKnowledge()
  const query = useTermDescription(instance, { iri, layerChain: true, includeUnpublished })
  const ref = useRef<HTMLDivElement>(null)
  // Following a link inside the panel replaces it: keep the keyboard focus here.
  useEffect(() => {
    if (onBack) ref.current?.focus({ preventScroll: true })
  }, [])
  const description = query.data?.description
  const chain = query.data?.layerChain

  return (
    <div ref={ref} tabIndex={-1} style={{ outline: 'none' }}>
      {onBack && (
        <Button size="small" type="text" icon={<ArrowLeftOutlined />} onClick={onBack} style={{ marginBottom: 6 }}>
          Back
        </Button>
      )}
      {query.isLoading ? (
        <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>
      ) : query.isError ? (
        <Alert
          type={knowledgeErrorStatus(query.error) === 404 ? 'info' : 'error'}
          showIcon
          message={iri}
          description={
            <>
              {knowledgeErrorMessage(query.error)}
              {knowledgeErrorStatus(query.error) === 404 && !includeUnpublished && (
                <div style={{ marginTop: 4 }}>Only published modules are searched: include the unpublished ones to look there too.</div>
              )}
            </>
          }
        />
      ) : description ? (
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          <TermHeader
            iri={iri}
            description={description}
            layer={description.layer ?? description.definedIn?.map(layerOf).find(Boolean)}
          />
          <LayerChain chain={chain?.chain ?? []} endsAtFoundational={chain?.endsAtFoundational === true} nameOf={nameOf} layerOf={layerOf} onOpen={onOpen} current={description.curie} />
          <Descriptions column={1} size="small" bordered labelStyle={{ width: 130 }}>
            <Descriptions.Item label="Labels"><Values values={description.labels} /></Descriptions.Item>
            <Descriptions.Item label="Definitions">
              {description.definitions?.length ? description.definitions.map((text) => <div key={text}>{text}</div>) : <Muted>None</Muted>}
            </Descriptions.Item>
            <Descriptions.Item label="Types"><Values values={description.types} /></Descriptions.Item>
            <Descriptions.Item label="Parents"><TermLinks terms={description.parents} onOpen={onOpen} /></Descriptions.Item>
            <Descriptions.Item label="Matches">
              {description.matches?.length ? (
                description.matches.map((match, i) => (
                  <div key={i}>
                    <code>{match.predicate}</code> <TermLinks terms={match.object ? [match.object] : []} onOpen={onOpen} />
                  </div>
                ))
              ) : (
                <Muted>None</Muted>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="Defined in"><Values values={description.definedIn?.map((id) => nameOf(id) ?? id)} /></Descriptions.Item>
          </Descriptions>
        </Space>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No description" />
      )}
    </div>
  )
}

function TermHeader({ iri, description, layer }: { iri: string; description: TermDescription; layer?: OntologyLayer }) {
  return (
    <div>
      <Space size={8} wrap>
        <span style={{ fontSize: 16, fontWeight: 650, color: colors.textPrimary }}>
          {description.labels?.[0] ?? description.curie ?? iri}
        </span>
        {layer && <LayerTag layer={layer} />}
        {description.linkStatus && <LinkStatusTag status={description.linkStatus} />}
      </Space>
      <div style={{ fontSize: 12, color: colors.textMuted, wordBreak: 'break-all' }}>
        {description.curie && description.curie !== description.iri && <code>{description.curie}</code>} {description.iri ?? iri}
      </div>
    </div>
  )
}

/** The subclass chain, from the term up, each entry in its layer's colour. */
function LayerChain({
  chain,
  endsAtFoundational,
  nameOf,
  layerOf,
  onOpen,
  current,
}: {
  chain: LayerChainEntry[]
  endsAtFoundational: boolean
  nameOf: (id: string | undefined) => string | undefined
  layerOf: (id: string | undefined) => OntologyLayer | undefined
  onOpen: (iri: string) => void
  current: string | undefined
}) {
  if (chain.length === 0) return null
  return (
    <div style={{ background: colors.surfaceSubtle, borderRadius: 10, padding: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 650, color: colors.textSecondary }}>Layer chain</span>
        {endsAtFoundational ? (
          <Tag color="green" style={{ marginInlineEnd: 0 }}>ends in the Foundational layer</Tag>
        ) : (
          <Tag color="orange" style={{ marginInlineEnd: 0 }}>doesn't reach the Foundational layer</Tag>
        )}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        {chain.map((entry, i) => {
          const layer = entry.layer ?? layerOf(entry.module)
          const color = graphColor(layer, undefined)
          return (
            <React.Fragment key={`${entry.term}-${i}`}>
              {i > 0 && <ArrowRightOutlined aria-label="is a subclass of" style={{ color: colors.textMuted, fontSize: 11 }} />}
              <button
                type="button"
                disabled={!entry.term || entry.term === current}
                onClick={() => entry.term && onOpen(entry.term)}
                style={{
                  textAlign: 'left',
                  border: `1px solid ${color.fill}`,
                  borderStyle: entry.linkStatus === 'provisional' ? 'dashed' : 'solid',
                  background: color.tint,
                  borderRadius: 8,
                  padding: '6px 10px',
                  cursor: !entry.term || entry.term === current ? 'default' : 'pointer',
                  font: 'inherit',
                }}
              >
                <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: 0.5, color: color.fill }}>
                  {(layer ?? 'no layer').toUpperCase()}
                </div>
                <div style={{ fontSize: 12, fontWeight: 600, color: colors.textPrimary }}>{entry.term}</div>
                <div style={{ fontSize: 10, color: colors.textMuted }}>
                  {nameOf(entry.module) ?? 'not declared in a module'}
                  {entry.linkStatus === 'provisional' && ' · provisional'}
                </div>
              </button>
            </React.Fragment>
          )
        })}
      </div>
    </div>
  )
}

function Values({ values }: { values: string[] | undefined }) {
  if (!values?.length) return <Muted>None</Muted>
  return <Space size={4} wrap>{values.map((value) => <NeutralTag key={value}>{value}</NeutralTag>)}</Space>
}

function TermLinks({ terms, onOpen }: { terms: string[] | undefined; onOpen: (iri: string) => void }) {
  if (!terms?.length) return <Muted>None</Muted>
  return (
    <Space size={8} wrap>
      {terms.map((value) =>
        looksLikeTerm(value) ? (
          <Button key={value} type="link" size="small" style={{ padding: 0 }} onClick={() => onOpen(value)}>{value}</Button>
        ) : (
          <span key={value}>{value}</span>
        ),
      )}
    </Space>
  )
}
