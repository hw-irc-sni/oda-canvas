import React, { useState } from 'react'
import { Drawer, Space, Tabs } from 'antd'
import { useKnowledgeBase, type Knowledge } from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import { StatusTag } from '../shared'
import { baseName } from './bases'
import DocumentsTab from './DocumentsTab'
import JobsTab from './JobsTab'
import { useBaseProcesses } from './jobs'
import OverviewTab from './OverviewTab'
import ProposalsTab from './ProposalsTab'
import SourcesTab from './SourcesTab'

interface KnowledgeBaseDrawerProps {
  base: Knowledge | undefined
  onClose: () => void
  onEdit: (base: Knowledge) => void
  onDelete: (base: Knowledge) => void
}

export default function KnowledgeBaseDrawer({ base, onClose, onEdit, onDelete }: KnowledgeBaseDrawerProps) {
  return (
    <Drawer
      open={!!base}
      onClose={onClose}
      width="min(1040px, 92vw)"
      title={
        base && (
          <Space size={8} wrap>
            <span>{baseName(base)}</span>
            <StatusTag status={base.lifecycleStatus} />
          </Space>
        )
      }
    >
      {base && <Details key={base.id} listed={base} onEdit={onEdit} onDelete={onDelete} />}
    </Drawer>
  )
}

function Details({ listed, onEdit, onDelete }: Pick<KnowledgeBaseDrawerProps, 'onEdit' | 'onDelete'> & { listed: Knowledge }) {
  const { instance, can } = useKnowledge()
  const [tab, setTab] = useState('overview')
  const query = useKnowledgeBase(instance, listed.id, { enabled: can('retrieveKnowledge') })
  // The list may have been refreshed after the knowledge-base was read on its own.
  const base = query.data && (query.data.lastUpdate ?? '') >= (listed.lastUpdate ?? '') ? query.data : listed
  const jobs = useBaseProcesses(base.id)
  const tabs = [
    {
      key: 'overview',
      label: 'Overview',
      show: true,
      content: <OverviewTab base={base} onEdit={() => onEdit(base)} onDelete={() => onDelete(base)} />,
    },
    {
      key: 'documents',
      label: 'Documents',
      show: can('listKnowledgeDocument'),
      content: <DocumentsTab base={base} running={jobs.running} />,
    },
    {
      key: 'sources',
      label: 'Sources and ingestion',
      show: can('createKnowledgeIngestion'),
      content: <SourcesTab base={base} onStarted={() => setTab('jobs')} />,
    },
    { key: 'jobs', label: jobs.running ? 'Jobs (running)' : 'Jobs', show: can('listProcess'), content: <JobsTab base={base} jobs={jobs} /> },
    { key: 'proposals', label: 'Proposals', show: can('listKnowledgeProposal'), content: <ProposalsTab base={base} /> },
  ]
  return (
    <Tabs
      activeKey={tab}
      onChange={setTab}
      items={tabs.filter((t) => t.show).map(({ key, label, content }) => ({ key, label, children: content }))}
    />
  )
}
