import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Drawer, Space, Spin, Tabs } from 'antd'
import { AuditOutlined } from '@ant-design/icons'
import { useModuleContent, type KnowledgeSpecification } from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import { CodeBlock, LayerTag, LinkStatusTag, Muted, NeutralTag, QueryError, StatusTag, formatDate } from '../shared'
import ModuleActions from './ModuleActions'
import ModuleGraph from './ModuleGraph'
import { describeSource, isFederated, moduleName, resolveRef } from './modules'

interface ModuleDrawerProps {
  module: KnowledgeSpecification | undefined
  modules: KnowledgeSpecification[]
  onClose: () => void
  onSelect: (id: string) => void
  /** Opens the module in the review queue. */
  onReview?: (id: string) => void
}

export default function ModuleDrawer({ module, modules, onClose, onSelect, onReview }: ModuleDrawerProps) {
  return (
    <Drawer
      open={!!module}
      onClose={onClose}
      width="min(1040px, 92vw)"
      title={
        module && (
          <Space size={8} wrap>
            <span>{moduleName(module)}</span>
            <StatusTag status={module.lifecycleStatus} />
          </Space>
        )
      }
      extra={
        module && (
          <Space size={8}>
            {onReview && !isFederated(module) && (
              <Button size="small" icon={<AuditOutlined />} onClick={() => onReview(module.id)}>Review</Button>
            )}
            <ModuleActions key={module.id} module={module} modules={modules} onDeleted={onClose} />
          </Space>
        )
      }
    >
      {/* Keyed, so that opening another module starts on its overview. */}
      {module && <ModuleDetails key={module.id} module={module} modules={modules} onSelect={onSelect} />}
    </Drawer>
  )
}

function ModuleDetails({ module, modules, onSelect }: Pick<ModuleDrawerProps, 'modules' | 'onSelect'> & { module: KnowledgeSpecification }) {
  const { can } = useKnowledge()
  const [tab, setTab] = useState('overview')
  const ref = useRef<HTMLDivElement>(null)
  // Following a link to another module removes the focused link; without this the focus leaves the drawer, and
  // Escape no longer closes it.
  useEffect(() => ref.current?.focus({ preventScroll: true }), [])
  return (
    <div ref={ref} tabIndex={-1} style={{ outline: 'none' }}>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          { key: 'overview', label: 'Overview', children: <Overview module={module} modules={modules} onSelect={onSelect} /> },
          // The content of a remote module stays at its peer.
          ...(can('retrieveKnowledgeSpecificationContent') && module.specKind !== 'remote'
            ? [{ key: 'graph', label: 'Graph', children: tab === 'graph' && <ModuleGraph module={module} /> }]
            : []),
          ...(can('retrieveKnowledgeSpecificationContent')
            ? [{ key: 'content', label: 'Content', children: tab === 'content' && <Content module={module} /> }]
            : []),
        ]}
      />
    </div>
  )
}

function Overview({ module, modules, onSelect }: { module: KnowledgeSpecification; modules: KnowledgeSpecification[]; onSelect: (id: string) => void }) {
  const importedBy = modules.filter((m) => (m.imports ?? []).some((ref) => resolveRef(ref, modules)?.id === module.id))
  const owner = module.owner?.partyOrPartyRole
  const links = (items: { id?: string; label: string }[]) =>
    items.length === 0 ? (
      <Muted>None</Muted>
    ) : (
      <Space size={4} wrap>
        {items.map((item) =>
          item.id ? (
            <Button key={item.label} type="link" size="small" style={{ padding: 0 }} onClick={() => onSelect(item.id!)}>
              {item.label}
            </Button>
          ) : (
            <NeutralTag key={item.label} title="Not registered on this instance">{item.label}</NeutralTag>
          ),
        )}
      </Space>
    )
  const refs = (list: KnowledgeSpecification['imports']) =>
    (list ?? []).map((ref) => {
      const target = resolveRef(ref, modules)
      return { id: target?.id, label: target ? moduleName(target) : ref.name || ref.iri || ref.id || 'unknown' }
    })

  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      {isFederated(module) && (
        <Alert
          type="info"
          showIcon
          message="Governed by its federation repository"
          description={`This module is synchronised from its source and is read-only here.${module.sourceStatus ? ` Its status in the source is "${module.sourceStatus}".` : ''}`}
        />
      )}
      {module.description && <div>{module.description}</div>}
      <Descriptions column={1} size="small" bordered labelStyle={{ width: 150 }}>
        <Descriptions.Item label="IRI"><code style={{ wordBreak: 'break-all' }}>{module.iri ?? '—'}</code></Descriptions.Item>
        <Descriptions.Item label="Id"><code>{module.id}</code></Descriptions.Item>
        <Descriptions.Item label="Prefix">{module.prefix ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="Version">{module.version ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="Layer"><LayerTag layer={module.layer} /></Descriptions.Item>
        <Descriptions.Item label="Kind">{module.specKind ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="Depth">{module.depth ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="Link status"><LinkStatusTag status={module.linkStatus} /></Descriptions.Item>
        <Descriptions.Item label="Owner">{owner?.name || owner?.partyName || owner?.id || '—'}</Descriptions.Item>
        <Descriptions.Item label="Source"><span style={{ wordBreak: 'break-all' }}>{describeSource(module.source)}</span></Descriptions.Item>
        <Descriptions.Item label="Last update">{formatDate(module.lastUpdate)}</Descriptions.Item>
        <Descriptions.Item label="Imports">{links(refs(module.imports))}</Descriptions.Item>
        <Descriptions.Item label="Imported by">{links(importedBy.map((m) => ({ id: m.id, label: moduleName(m) })))}</Descriptions.Item>
        {(module.describes?.length ?? 0) > 0 && (
          <Descriptions.Item label="Describes">{links(refs(module.describes))}</Descriptions.Item>
        )}
      </Descriptions>
    </Space>
  )
}

function Content({ module }: { module: KnowledgeSpecification }) {
  const { instance } = useKnowledge()
  const query = useModuleContent(instance, module.id)
  if (query.isLoading) return <Spin />
  if (query.isError) return <QueryError error={query.error} what="the content" onRetry={() => query.refetch()} />
  if (module.specKind === 'remote') return <Muted>The content of a remote module stays at its peer.</Muted>
  if (!query.data?.trim()) return <Muted>This module has no content yet.</Muted>
  return <CodeBlock text={query.data} filename={`${module.id}.ttl`} maxHeight={560} />
}
