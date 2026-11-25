import React, { useEffect, useMemo, useState } from 'react'
import { Button, Segmented, Select, Space, Spin, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ReloadOutlined } from '@ant-design/icons'
import { useMappingsOf, useModules, type LifecycleStatus } from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { LIFECYCLE_STATUSES, Muted, NeutralTag, QueryError, StatusTag, statusLabel } from '../shared'
import { isFederated, moduleName } from './modules'
import ReviewDrawer from './ReviewDrawer'
import { ACTION_LABELS, mappingSubject, moduleSubject, reviewActions, type ReviewSubject } from './reviews'

const WAITING: LifecycleStatus[] = ['proposed', 'inReview']

interface ReviewQueueProps {
  /** A module to open, from its details. */
  openModule?: string
  onOpened: () => void
}

/** The modules and mappings in the governed lifecycle: propose, review and rule. */
export default function ReviewQueue({ openModule, onOpened }: ReviewQueueProps) {
  const access = useKnowledge()
  const modules = useModules(access.instance)
  // Federated modules are governed by their repository: the component refuses any change of theirs.
  const governed = useMemo(() => (modules.data ?? []).filter((m) => !isFederated(m)), [modules.data])
  const bridges = governed.filter((m) => m.specKind === 'bridge')
  const mappings = useMappingsOf(access.instance, access.can('listKnowledgeSpecificationMapping') ? bridges.map((m) => m.id) : [])
  const [statuses, setStatuses] = useState<LifecycleStatus[]>(WAITING)
  const [kind, setKind] = useState<'all' | 'module' | 'mapping'>('all')
  const [mineOnly, setMineOnly] = useState(false)
  const [selected, setSelected] = useState<string>()

  useEffect(() => {
    if (!openModule) return
    setSelected(`module:${openModule}`)
    onOpened()
  }, [openModule])

  const subjects: ReviewSubject[] = [
    ...governed.map(moduleSubject),
    ...bridges.flatMap((bridge, i) => (mappings[i]?.data ?? []).map((m) => mappingSubject(bridge, m))),
  ]
  const shown = subjects
    .filter((s) => statuses.length === 0 || (s.status && statuses.includes(s.status)))
    .filter((s) => kind === 'all' || s.target.kind === kind)
    .filter((s) => !mineOnly || reviewActions(access, s.status).length > 0)
    .sort((a, b) => LIFECYCLE_STATUSES.indexOf(b.status!) - LIFECYCLE_STATUSES.indexOf(a.status!) || a.title.localeCompare(b.title))
  const failed = mappings.find((m) => m.isError)

  if (modules.isLoading) return <div style={{ padding: 60, textAlign: 'center' }}><Spin /></div>
  if (modules.isError && !modules.data) return <QueryError error={modules.error} what="the modules" onRetry={() => modules.refetch()} />

  const columns: ColumnsType<ReviewSubject> = [
    {
      title: 'Item',
      key: 'item',
      render: (_, s) => (
        <div>
          <div style={{ fontWeight: 600, color: colors.textPrimary, wordBreak: 'break-word' }}>{s.title}</div>
          <div style={{ fontSize: 11, color: colors.textMuted, wordBreak: 'break-all' }}>
            {s.mapping ? `in ${moduleName(s.module)}` : s.module.iri}
          </div>
        </div>
      ),
    },
    { title: 'Type', key: 'type', width: 110, render: (_, s) => <NeutralTag>{s.mapping ? 'mapping' : s.module.specKind ?? 'module'}</NeutralTag> },
    { title: 'Status', key: 'status', width: 120, render: (_, s) => <StatusTag status={s.status} /> },
    {
      title: 'You can',
      key: 'actions',
      width: 240,
      render: (_, s) => {
        const actions = reviewActions(access, s.status)
        if (actions.length === 0) return <Muted>—</Muted>
        return <Space size={4} wrap>{actions.map((a) => <Tag key={a} color="blue" style={{ marginInlineEnd: 0 }}>{ACTION_LABELS[a]}</Tag>)}</Space>
      },
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
        <Space wrap size={8}>
          <Select
            mode="multiple"
            allowClear
            placeholder="All statuses"
            aria-label="Review statuses"
            value={statuses}
            onChange={setStatuses}
            options={LIFECYCLE_STATUSES.map((value) => ({ value, label: statusLabel(value) }))}
            style={{ minWidth: 240 }}
          />
          <Segmented
            value={kind}
            onChange={(value) => setKind(value as typeof kind)}
            options={[{ label: 'All', value: 'all' }, { label: 'Modules', value: 'module' }, { label: 'Mappings', value: 'mapping' }]}
          />
          <Segmented
            value={mineOnly ? 'mine' : 'all'}
            onChange={(value) => setMineOnly(value === 'mine')}
            options={[{ label: 'Everything', value: 'all' }, { label: 'I can act on', value: 'mine' }]}
          />
        </Space>
        <Button
          icon={<ReloadOutlined />}
          loading={modules.isFetching || mappings.some((m) => m.isFetching)}
          onClick={() => {
            modules.refetch()
            mappings.forEach((m) => m.refetch())
          }}
        >
          Refresh
        </Button>
      </div>
      {failed && <div style={{ marginBottom: 12 }}><QueryError error={failed.error} what="some mappings" onRetry={() => failed.refetch()} /></div>}
      <Table<ReviewSubject>
        rowKey="key"
        size="middle"
        columns={columns}
        dataSource={shown}
        loading={mappings.some((m) => m.isLoading)}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: statuses.length ? 'Nothing is in these states.' : 'Nothing to review.' }}
        onRow={(s) => ({
          onClick: () => setSelected(s.key),
          onKeyDown: (e) => {
            if (e.key === 'Enter') setSelected(s.key)
          },
          tabIndex: 0,
          style: { cursor: 'pointer' },
        })}
      />
      <ReviewDrawer subject={subjects.find((s) => s.key === selected)} onClose={() => setSelected(undefined)} />
    </div>
  )
}
