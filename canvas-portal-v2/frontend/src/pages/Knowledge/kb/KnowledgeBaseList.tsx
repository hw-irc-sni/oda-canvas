import React, { useMemo, useState } from 'react'
import { Button, Input, Space, Spin, Table, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useKnowledgeBases, type Knowledge } from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { LIFECYCLE_STATUSES, Muted, NeutralTag, QueryError, StatusTag, formatDate } from '../shared'
import { SOURCE_LABELS, baseName, hasVectors, mayEdit } from './bases'
import DeleteBaseModal from './DeleteBaseModal'
import KnowledgeBaseDrawer from './KnowledgeBaseDrawer'
import KnowledgeBaseForm from './KnowledgeBaseForm'

const count = (value: number | undefined) => (value === undefined ? '—' : value.toLocaleString())

/** The knowledge-bases of the instance. */
export default function KnowledgeBaseList() {
  const access = useKnowledge()
  const query = useKnowledgeBases(access.instance)
  const [text, setText] = useState('')
  const [editing, setEditing] = useState<Knowledge | 'new'>()
  const [deleting, setDeleting] = useState<Knowledge>()
  const [selected, setSelected] = useState<string>()
  const bases = query.data ?? []
  const shown = useMemo(() => {
    const needle = text.trim().toLowerCase()
    return bases.filter((kb) => !needle || [kb.name, kb.id, kb.description].some((v) => v?.toLowerCase().includes(needle)))
  }, [query.data, text])

  if (query.isLoading) return <div style={{ padding: 60, textAlign: 'center' }}><Spin /></div>
  if (query.isError && !query.data) return <QueryError error={query.error} what="the knowledge-bases" onRetry={() => query.refetch()} />

  const columns: ColumnsType<Knowledge> = [
    {
      title: 'Knowledge-base',
      key: 'name',
      sorter: (a, b) => baseName(a).localeCompare(baseName(b)),
      defaultSortOrder: 'ascend',
      render: (_, kb) => (
        <div>
          <div style={{ fontWeight: 600, color: colors.textPrimary }}>{baseName(kb)}</div>
          <div style={{ fontSize: 11, color: colors.textMuted }}>{kb.description || kb.id}</div>
        </div>
      ),
    },
    {
      title: 'Categories',
      key: 'categories',
      width: 170,
      render: (_, kb) =>
        kb.knowledgeCategory?.length ? (
          <Space size={4} wrap>{kb.knowledgeCategory.map((c) => <NeutralTag key={c}>{c}</NeutralTag>)}</Space>
        ) : (
          <Muted>—</Muted>
        ),
    },
    { title: 'Store', dataIndex: 'storeKind', key: 'storeKind', width: 85, render: (value?: string) => value ?? '—' },
    {
      title: 'Status',
      key: 'status',
      width: 110,
      sorter: (a, b) => LIFECYCLE_STATUSES.indexOf(a.lifecycleStatus!) - LIFECYCLE_STATUSES.indexOf(b.lifecycleStatus!),
      render: (_, kb) => <StatusTag status={kb.lifecycleStatus} />,
    },
    {
      title: 'Conforms to',
      key: 'conformsTo',
      width: 160,
      render: (_, kb) => kb.conformsTo?.map((ref) => ref.name ?? ref.id).join(', ') || <Muted>—</Muted>,
    },
    {
      title: 'Sources',
      key: 'source',
      width: 120,
      render: (_, kb) =>
        kb.source?.length ? (
          <Space size={4} wrap>
            {kb.source.map((s, i) => <NeutralTag key={i} title={SOURCE_LABELS[s.kind]}>{s.kind}</NeutralTag>)}
          </Space>
        ) : (
          <Muted>—</Muted>
        ),
    },
    {
      title: 'Content',
      key: 'statistics',
      width: 140,
      render: (_, kb) => (
        <div style={{ fontSize: 12, color: colors.textSecondary }}>
          <div>{count(kb.statistics?.triples)} triples</div>
          <div>{count(kb.statistics?.documents)} documents</div>
          {hasVectors(kb) && <div>{count(kb.statistics?.chunks)} chunks</div>}
        </div>
      ),
    },
    {
      title: 'Last update',
      dataIndex: 'lastUpdate',
      key: 'lastUpdate',
      width: 165,
      sorter: (a, b) => (a.lastUpdate ?? '').localeCompare(b.lastUpdate ?? ''),
      render: (value?: string) => formatDate(value),
    },
    {
      title: '',
      key: 'actions',
      width: 90,
      render: (_, kb) => (
        <Space size={4} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          {mayEdit(access, kb) && (
            <Tooltip title="Edit"><Button size="small" aria-label={`Edit ${kb.id}`} icon={<EditOutlined />} onClick={() => setEditing(kb)} /></Tooltip>
          )}
          {access.can('deleteKnowledge') && (
            <Tooltip title="Delete">
              <Button size="small" danger aria-label={`Delete ${kb.id}`} icon={<DeleteOutlined />} onClick={() => setDeleting(kb)} />
            </Tooltip>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
        <Input.Search
          allowClear
          placeholder="Name or description"
          aria-label="Filter knowledge-bases"
          value={text}
          onChange={(e) => setText(e.target.value)}
          style={{ width: 260 }}
        />
        <Space size={8}>
          <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>Refresh</Button>
          {access.can('createKnowledge') && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>New knowledge-base</Button>
          )}
        </Space>
      </div>
      {query.isError && <div style={{ marginBottom: 12 }}><QueryError error={query.error} what="the latest knowledge-bases" /></div>}
      <Table<Knowledge>
        rowKey="id"
        size="middle"
        columns={columns}
        dataSource={shown}
        scroll={{ x: 1160 }}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: bases.length === 0 ? 'No knowledge-base yet.' : 'No knowledge-base matches.' }}
        onRow={(kb) => ({
          onClick: () => setSelected(kb.id),
          onKeyDown: (e) => {
            if (e.key === 'Enter') setSelected(kb.id)
          },
          tabIndex: 0,
          style: { cursor: 'pointer' },
        })}
      />
      <KnowledgeBaseDrawer
        base={bases.find((kb) => kb.id === selected)}
        onClose={() => setSelected(undefined)}
        onEdit={setEditing}
        onDelete={setDeleting}
      />
      {editing && <KnowledgeBaseForm base={editing === 'new' ? undefined : editing} onClose={() => setEditing(undefined)} />}
      {deleting && <DeleteBaseModal base={deleting} onClose={() => setDeleting(undefined)} />}
    </div>
  )
}
