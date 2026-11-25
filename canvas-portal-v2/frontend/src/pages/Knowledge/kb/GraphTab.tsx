import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Descriptions, Dropdown, Empty, Input, List, Modal, Popconfirm, Radio, Space, Spin, Switch, message } from 'antd'
import { ClearOutlined, DownloadOutlined, ImportOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import {
  fetchKnowledgeGraph,
  knowledgeErrorMessage,
  knowledgeScopeKey,
  queryKnowledge,
  saveBlob,
  useClearKnowledgeGraph,
  useMergeKnowledgeGraph,
  useReplaceKnowledgeGraph,
  type Knowledge,
  type RdfMediaType,
  type SparqlResults,
} from '@/api/knowledge'
import KnowledgeGraph from '@/components/KnowledgeGraph'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import TurtleInput from '../ontology/TurtleInput'
import { Muted, QueryError } from '../shared'
import { isWritable } from './bases'
import {
  ENTITY_LIMIT,
  NEIGHBOUR_LIMIT,
  entitiesQuery,
  isIri,
  localName,
  neighbourhoodQuery,
  termDetail,
  termText,
  toGraph,
  toNeighbourhood,
  type Neighbourhood,
} from './sparql'

const MAX_EXPANDED = 12

/** A SELECT over the knowledge-base, read again when it changes. */
function useSelect(id: string, name: string, query: string, enabled: boolean) {
  const { instance } = useKnowledge()
  return useQuery({
    queryKey: [...knowledgeScopeKey(instance), 'item', id, 'select', name, query],
    enabled,
    retry: false,
    queryFn: async (): Promise<SparqlResults> => {
      const answer = await queryKnowledge(instance, { id, query })
      return answer.kind === 'results' ? answer.results : {}
    },
  })
}

interface GraphTabProps {
  base: Knowledge
  /** The entity to go to, from a search; a new object for each request, so that the same entity can be asked again. */
  target?: { iri: string }
}

/** Explores the neighbourhood of the knowledge-base's entities. */
export default function GraphTab({ base, target }: GraphTabProps) {
  const { can } = useKnowledge()
  const [typed, setTyped] = useState('')
  const [text, setText] = useState('')
  const [focus, setFocus] = useState<string>()
  // The neighbourhoods drawn so far, the last expanded last.
  const [drawn, setDrawn] = useState<{ iri: string; data: Neighbourhood }[]>([])
  const [showProvenance, setShowProvenance] = useState(false)
  const explorable = can('queryKnowledge')

  useEffect(() => {
    if (target) setFocus(target.iri)
  }, [target])

  const entities = useSelect(base.id, 'entities', entitiesQuery(text), explorable)
  const current = useSelect(base.id, 'neighbourhood', focus && isIri(focus) ? neighbourhoodQuery(focus) : '', explorable && !!focus && isIri(focus))
  useEffect(() => {
    if (!focus || !current.data) return
    const data = toNeighbourhood(focus, current.data)
    setDrawn((before) => [...before.filter((n) => n.iri !== focus), { iri: focus, data }].slice(-MAX_EXPANDED))
  }, [focus, current.data])

  const graph = useMemo(() => toGraph(drawn.map((n) => n.data), { showProvenance }), [drawn, showProvenance])
  const details = drawn.find((n) => n.iri === focus)?.data
  const rows = entities.data?.results?.bindings ?? []

  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <GraphActions base={base} />
      {!explorable ? (
        <Muted>Your roles don't include querying this knowledge-base.</Muted>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(200px, 240px) minmax(0, 1fr)', gap: 16, alignItems: 'start' }}>
          <div>
            <Input.Search
              allowClear
              aria-label="Find an entity"
              placeholder="Label, or an IRI"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onSearch={(value) => {
                const wanted = value.trim()
                // A whole IRI is opened directly: it may be one the list doesn't show, such as a class.
                if (/^(https?|urn):/.test(wanted) && isIri(wanted)) setFocus(wanted)
                else setText(wanted)
              }}
              style={{ marginBottom: 8 }}
            />
            {entities.isError ? (
              <QueryError error={entities.error} what="the entities" onRetry={() => entities.refetch()} />
            ) : (
              <List
                size="small"
                bordered
                loading={entities.isFetching}
                dataSource={rows}
                locale={{ emptyText: text ? 'No entity matches' : 'No entity yet' }}
                style={{ maxHeight: 460, overflow: 'auto' }}
                renderItem={({ entity: e, label, type }) => (
                  <List.Item
                    tabIndex={0}
                    onClick={() => setFocus(e.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') setFocus(e.value)
                    }}
                    style={{ cursor: 'pointer', background: e.value === focus ? colors.surfaceSelected : undefined }}
                  >
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 600, wordBreak: 'break-word' }}>{label?.value ?? localName(e.value)}</div>
                      <div style={{ fontSize: 11, color: colors.textMuted }}>{type ? localName(type.value) : ''}</div>
                    </div>
                  </List.Item>
                )}
                footer={rows.length >= ENTITY_LIMIT && <Muted>The first {ENTITY_LIMIT}: search to narrow down.</Muted>}
              />
            )}
          </div>

          <div style={{ minWidth: 0 }}>
            {current.isError && (
              <Alert type="error" showIcon style={{ marginBottom: 10 }} message={knowledgeErrorMessage(current.error)} />
            )}
            {drawn.length === 0 ? (
              <div style={{ border: `1px dashed ${colors.border}`, borderRadius: 12, padding: 60 }}>
                {current.isFetching ? <div style={{ textAlign: 'center' }}><Spin /></div> : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Select an entity to see what it is linked to" />
                )}
              </div>
            ) : (
              <KnowledgeGraph
                nodes={graph.nodes}
                edges={graph.edges}
                selected={focus}
                onSelect={(node) => setFocus(node.iri)}
                height={440}
                exportName={`${base.id}-graph`}
                emptyText="This entity is linked to nothing"
                title={
                  <Space size={8}>
                    <Switch size="small" aria-label="Show provenance" checked={showProvenance} onChange={setShowProvenance} />
                    <span style={{ fontSize: 12, color: colors.textSecondary }}>Provenance</span>
                    <Button
                      size="small"
                      onClick={() => {
                        setDrawn([])
                        setFocus(undefined)
                      }}
                    >
                      Start over
                    </Button>
                    {current.isFetching && <Spin size="small" />}
                  </Space>
                }
              />
            )}
            {details?.cut && <Muted>Only the first {NEIGHBOUR_LIMIT} statements of this entity are read.</Muted>}
            {focus && details && <EntityDetails iri={focus} data={details} onOpen={setFocus} />}
          </div>
        </div>
      )}
    </Space>
  )
}

/** Every statement about the selected entity, values included. */
function EntityDetails({ iri, data, onOpen }: { iri: string; data: Neighbourhood; onOpen: (iri: string) => void }) {
  const values = new Map<string, Neighbourhood['statements']>()
  for (const statement of data.statements) {
    if (statement.s.value === iri) values.set(statement.p.value, [...(values.get(statement.p.value) ?? []), statement])
  }
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontWeight: 650 }}>{data.labels[iri] ?? localName(iri)}</div>
      <div style={{ fontSize: 12, color: colors.textMuted, wordBreak: 'break-all', marginBottom: 8 }}>{iri}</div>
      {values.size === 0 ? (
        <Muted>Nothing is stated about it here.</Muted>
      ) : (
        <Descriptions column={1} size="small" bordered labelStyle={{ width: 170 }}>
          {[...values.entries()].map(([predicate, statements]) => (
            <Descriptions.Item key={predicate} label={<span title={predicate}>{localName(predicate)}</span>}>
              {statements.map(({ o }, i) => (
                <div key={i} style={{ wordBreak: 'break-word' }}>
                  {o.type === 'uri' && isIri(o.value) ? (
                    <Button type="link" size="small" title={o.value} style={{ padding: 0 }} onClick={() => onOpen(o.value)}>
                      {data.labels[o.value] ?? localName(o.value)}
                    </Button>
                  ) : (
                    <>
                      {termText(o)} {termDetail(o) && <Muted>{termDetail(o)}</Muted>}
                    </>
                  )}
                </div>
              ))}
            </Descriptions.Item>
          ))}
        </Descriptions>
      )}
    </div>
  )
}

// ---- import, export, clear -----------------------------------------------------------------------------------

const EXPORTS: { mediaType: RdfMediaType; label: string; extension: string }[] = [
  { mediaType: 'text/turtle', label: 'Turtle', extension: 'ttl' },
  { mediaType: 'application/ld+json', label: 'JSON-LD', extension: 'jsonld' },
]

function GraphActions({ base }: { base: Knowledge }) {
  const access = useKnowledge()
  const clear = useClearKnowledgeGraph(access.instance)
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const writable = isWritable(base)
  const canMerge = writable && access.can('mergeKnowledgeGraph')
  const canReplace = writable && access.can('replaceKnowledgeGraph')

  async function download({ mediaType, extension }: (typeof EXPORTS)[number]) {
    setExporting(true)
    try {
      saveBlob(new Blob([await fetchKnowledgeGraph(access.instance, base.id, mediaType)]), `${base.id}.${extension}`)
    } catch (error) {
      message.error(knowledgeErrorMessage(error), 6)
    } finally {
      setExporting(false)
    }
  }

  return (
    <Space size={8} wrap>
      {access.can('retrieveKnowledgeGraph') && (
        <Dropdown menu={{ items: EXPORTS.map((e) => ({ key: e.extension, label: e.label, onClick: () => download(e) })) }}>
          <Button icon={<DownloadOutlined />} loading={exporting}>Export graph</Button>
        </Dropdown>
      )}
      {(canMerge || canReplace) && <Button icon={<ImportOutlined />} onClick={() => setImporting(true)}>Import RDF</Button>}
      {writable && access.can('clearKnowledgeGraph') && (
        <Popconfirm
          title="Clear the graph?"
          description="Every triple of this knowledge-base is removed, those extracted from documents included."
          okText="Clear"
          okButtonProps={{ danger: true }}
          onConfirm={() =>
            clear.mutateAsync(base.id).then(
              () => message.success('The graph was cleared'),
              (error) => message.error(knowledgeErrorMessage(error), 6),
            )
          }
        >
          <Button danger icon={<ClearOutlined />} loading={clear.isPending}>Clear</Button>
        </Popconfirm>
      )}
      {importing && <ImportModal base={base} canMerge={canMerge} canReplace={canReplace} onClose={() => setImporting(false)} />}
    </Space>
  )
}

function ImportModal({ base, canMerge, canReplace, onClose }: { base: Knowledge; canMerge: boolean; canReplace: boolean; onClose: () => void }) {
  const { instance } = useKnowledge()
  const [content, setContent] = useState('')
  const [mediaType, setMediaType] = useState<RdfMediaType>('text/turtle')
  const [mode, setMode] = useState<'merge' | 'replace'>(canMerge ? 'merge' : 'replace')
  const merge = useMergeKnowledgeGraph(instance)
  const replace = useReplaceKnowledgeGraph(instance)
  const write = mode === 'merge' ? merge : replace
  return (
    <Modal
      open
      title="Import RDF"
      width={720}
      onCancel={onClose}
      okText={mode === 'merge' ? 'Add to the graph' : 'Replace the graph'}
      okButtonProps={{ disabled: !content.trim(), danger: mode === 'replace' }}
      confirmLoading={write.isPending}
      onOk={() =>
        write.mutate(
          { id: base.id, content, mediaType },
          {
            onSuccess: () => {
              message.success(mode === 'merge' ? 'The triples were added' : 'The graph was replaced')
              onClose()
            },
          },
        )
      }
    >
      <Space wrap size={12} style={{ marginBottom: 12 }}>
        <Radio.Group
          optionType="button"
          value={mode}
          onChange={(e) => setMode(e.target.value as 'merge' | 'replace')}
          options={[
            { value: 'merge', label: 'Add to the graph', disabled: !canMerge },
            { value: 'replace', label: 'Replace the graph', disabled: !canReplace },
          ]}
        />
        <Radio.Group
          optionType="button"
          value={mediaType}
          onChange={(e) => setMediaType(e.target.value as RdfMediaType)}
          options={EXPORTS.map((e) => ({ value: e.mediaType, label: e.label }))}
        />
      </Space>
      {mode === 'replace' && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="Every triple of this knowledge-base is replaced, those extracted from documents included." />
      )}
      <TurtleInput value={content} onChange={setContent} label="RDF content" accept=".ttl,.turtle,.jsonld,.json,text/turtle,application/ld+json" />
      {write.isError && <Alert style={{ marginTop: 12 }} type="error" showIcon message={knowledgeErrorMessage(write.error)} />}
    </Modal>
  )
}
