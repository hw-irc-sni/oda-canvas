import React, { useMemo, useState } from 'react'
import { Button, Input, Segmented, Select, Space, Spin, Table, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  useModules,
  type KnowledgeSpecification,
  type LifecycleStatus,
  type OntologyLayer,
  type SpecKind,
} from '@/api/knowledge'
import KnowledgeGraph from '@/components/KnowledgeGraph'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { LAYERS, LIFECYCLE_STATUSES, LayerTag, LinkStatusTag, QueryError, StatusTag, statusLabel } from '../shared'
import ModuleDrawer from './ModuleDrawer'
import ModuleUpload from './ModuleUpload'
import { describeSource, isFederated, moduleGraph, moduleName } from './modules'

const KINDS: SpecKind[] = ['ontology', 'vocabulary', 'shapes', 'bridge', 'remote']

const byText = (pick: (m: KnowledgeSpecification) => string | undefined) => (a: KnowledgeSpecification, b: KnowledgeSpecification) =>
  (pick(a) ?? '').localeCompare(pick(b) ?? '')

const columns: ColumnsType<KnowledgeSpecification> = [
  {
    title: 'Module',
    key: 'name',
    sorter: byText(moduleName),
    defaultSortOrder: 'ascend',
    render: (_, m) => (
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600, color: colors.textPrimary }}>{moduleName(m)}</div>
        <div style={{ fontSize: 11, color: colors.textMuted, wordBreak: 'break-all' }}>{m.iri}</div>
      </div>
    ),
  },
  { title: 'Prefix', dataIndex: 'prefix', key: 'prefix', width: 100, sorter: byText((m) => m.prefix), render: (v?: string) => v ?? '—' },
  { title: 'Version', dataIndex: 'version', key: 'version', width: 90, render: (v?: string) => v ?? '—' },
  { title: 'Layer', key: 'layer', width: 130, sorter: byText((m) => m.layer), render: (_, m) => <LayerTag layer={m.layer} /> },
  { title: 'Kind', dataIndex: 'specKind', key: 'specKind', width: 100, sorter: byText((m) => m.specKind), render: (v?: string) => v ?? '—' },
  {
    title: 'Status',
    key: 'lifecycleStatus',
    width: 110,
    sorter: (a, b) => LIFECYCLE_STATUSES.indexOf(a.lifecycleStatus!) - LIFECYCLE_STATUSES.indexOf(b.lifecycleStatus!),
    render: (_, m) => <StatusTag status={m.lifecycleStatus} />,
  },
  { title: 'Link status', key: 'linkStatus', width: 110, render: (_, m) => <LinkStatusTag status={m.linkStatus} /> },
  {
    title: 'Source',
    key: 'source',
    width: 140,
    render: (_, m) => (
      <Tooltip title={describeSource(m.source)}>
        <span>{isFederated(m) ? 'federated' : m.source?.kind ?? '—'}</span>
      </Tooltip>
    ),
  },
]

/** The ontology modules, as a graph by layer or as a table. */
export default function ModuleRegistry() {
  const { instance, can } = useKnowledge()
  const query = useModules(instance)
  const [view, setView] = useState<'graph' | 'table'>('graph')
  const [text, setText] = useState('')
  const [layers, setLayers] = useState<OntologyLayer[]>([])
  const [kinds, setKinds] = useState<SpecKind[]>([])
  const [statuses, setStatuses] = useState<LifecycleStatus[]>([])
  const [selected, setSelected] = useState<string>()
  const [registering, setRegistering] = useState(false)

  const modules = query.data ?? []
  const shown = useMemo(() => {
    const needle = text.trim().toLowerCase()
    return modules.filter(
      (m) =>
        (!needle || [m.name, m.id, m.iri, m.prefix, m.description].some((v) => v?.toLowerCase().includes(needle))) &&
        (layers.length === 0 || (m.layer && layers.includes(m.layer))) &&
        (kinds.length === 0 || (m.specKind && kinds.includes(m.specKind))) &&
        (statuses.length === 0 || (m.lifecycleStatus && statuses.includes(m.lifecycleStatus))),
    )
  }, [query.data, text, layers, kinds, statuses])
  const graph = useMemo(() => moduleGraph(shown), [shown])

  if (query.isLoading) return <div style={{ padding: 60, textAlign: 'center' }}><Spin /></div>
  if (query.isError && !query.data) return <QueryError error={query.error} what="the modules" onRetry={() => query.refetch()} />

  const emptyText = modules.length === 0 ? 'No ontology module is registered on this instance yet.' : 'No module matches the filters.'
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
        <Space wrap size={8}>
          <Segmented
            value={view}
            onChange={(value) => setView(value as 'graph' | 'table')}
            options={[{ label: 'Graph', value: 'graph' }, { label: 'Table', value: 'table' }]}
          />
          <Input.Search
            allowClear
            placeholder="Name, IRI or prefix"
            aria-label="Filter modules"
            value={text}
            onChange={(e) => setText(e.target.value)}
            style={{ width: 210 }}
          />
          <Select
            mode="multiple"
            allowClear
            placeholder="Layers"
            aria-label="Layers"
            value={layers}
            onChange={setLayers}
            options={LAYERS.map((value) => ({ value, label: value }))}
            style={{ minWidth: 130 }}
            maxTagCount="responsive"
          />
          <Select
            mode="multiple"
            allowClear
            placeholder="Kinds"
            aria-label="Kinds"
            value={kinds}
            onChange={setKinds}
            options={KINDS.map((value) => ({ value, label: value }))}
            style={{ minWidth: 120 }}
            maxTagCount="responsive"
          />
          <Select
            mode="multiple"
            allowClear
            placeholder="Statuses"
            aria-label="Statuses"
            value={statuses}
            onChange={setStatuses}
            options={LIFECYCLE_STATUSES.map((value) => ({ value, label: statusLabel(value) }))}
            style={{ minWidth: 130 }}
            maxTagCount="responsive"
          />
        </Space>
        <Space size={8}>
          <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>Refresh</Button>
          {can('createKnowledgeSpecification') && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setRegistering(true)}>Register module</Button>
          )}
        </Space>
      </div>

      {query.isError && <div style={{ marginBottom: 12 }}><QueryError error={query.error} what="the latest modules" /></div>}

      {view === 'graph' ? (
        <KnowledgeGraph
          nodes={graph.nodes}
          edges={graph.edges}
          bands={LAYERS}
          selected={selected}
          onSelect={(node) => setSelected(node.iri)}
          height={560}
          emptyText={emptyText}
          exportName="ontology-modules"
        />
      ) : (
        <Table<KnowledgeSpecification>
          rowKey="id"
          size="middle"
          columns={columns}
          dataSource={shown}
          scroll={{ x: 980 }}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          locale={{ emptyText }}
          onRow={(m) => ({
            onClick: () => setSelected(m.id),
            onKeyDown: (e) => {
              if (e.key === 'Enter') setSelected(m.id)
            },
            tabIndex: 0,
            style: { cursor: 'pointer' },
          })}
        />
      )}

      <ModuleDrawer
        module={modules.find((m) => m.id === selected)}
        modules={modules}
        onClose={() => setSelected(undefined)}
        onSelect={setSelected}
      />
      <ModuleUpload
        open={registering}
        modules={modules}
        onClose={() => setRegistering(false)}
        onOpenModule={(id) => {
          setRegistering(false)
          setSelected(id)
        }}
      />
    </div>
  )
}
