import React, { useState } from 'react'
import { Alert, Button, Empty, Space, Spin, Tag } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { knowledgeErrorMessage, useKnowledgeInstances, type KnowledgeInstance } from '@/api/knowledge'
import BrainOutlined from '@/components/BrainOutlined'
import SectionedPage, { type PageSection } from '@/components/SectionedPage'
import { colors } from '@/theme'
import InstancePicker, { pickInstance, saveInstance, savedInstance } from './InstancePicker'
import KnowledgeBaseSection from './KnowledgeBaseSection'
import { KnowledgeProvider } from './KnowledgeContext'
import OntologySection from './OntologySection'

const KNOWLEDGE_BASE = {
  key: 'knowledge-base',
  title: 'Knowledge-Base',
  description: 'Curated knowledge sources that agents retrieve from and reason over.',
}
const ONTOLOGY = {
  key: 'federated-ontology',
  title: 'Federated Ontology',
  description: 'Shared ontology federated across components, agents and domains.',
}

/** Why an instance's sections can't be shown, or null when they can. */
function problem(instance: KnowledgeInstance): { message: string; description?: string } | null {
  if (!instance.available) {
    const status = instance.deploymentStatus || instance.resourceStatus
    return {
      message: `${instance.label} is not available`,
      description: status ? `Its status is "${status}".` : 'It exposes no Knowledge Management API yet.',
    }
  }
  if (!instance.capabilities) {
    return {
      message: `${instance.label} can't be used right now`,
      description: instance.capabilitiesError ?? 'Its API description is unavailable.',
    }
  }
  if (instance.capabilities.roles.length === 0) {
    return {
      message: `You have no role on ${instance.label}`,
      description:
        'Ask an administrator for one of its roles (such as knowledge-viewer), then sign in again: roles are read from your session.',
    }
  }
  return null
}

function NoInstances({ discovery }: { discovery: 'ri' | 'static' }) {
  return (
    <div style={{ background: colors.bgCard, border: `1px solid ${colors.border}`, borderRadius: 12, padding: 40 }}>
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <div style={{ maxWidth: 560, margin: '0 auto', lineHeight: 1.6 }}>
            <div style={{ fontWeight: 650, color: colors.textPrimary, marginBottom: 6 }}>No knowledge instance found</div>
            <div style={{ fontSize: 12, color: colors.textSecondary }}>
              This page works with TMFC060 Knowledge Management components.{' '}
              {discovery === 'ri' ? (
                <>
                  Deploy one with its Helm chart (<code>source/components/TMFC060-KnowledgeManagement/chart</code>); it appears
                  here when Resource Inventory lists it, usually within a minute.
                </>
              ) : (
                <>
                  This portal uses a fixed list, which is empty: add an instance to <code>backend.knowledge.instances</code> in
                  its Helm values.
                </>
              )}
            </div>
          </div>
        }
      />
    </div>
  )
}

export default function KnowledgePage() {
  const query = useKnowledgeInstances()
  const [wanted, setWanted] = useState<string | null>(savedInstance)
  const instances = query.data?.instances ?? []
  const selected = pickInstance(instances, wanted)

  const header = {
    title: 'Knowledge',
    description: 'Knowledge-base and federated ontology',
    icon: <BrainOutlined />,
  }

  if (query.isLoading) {
    return (
      <SectionedPage
        {...header}
        sections={[]}
        banner={<div style={{ padding: 60, textAlign: 'center' }}><Spin /></div>}
      />
    )
  }
  if (!selected) {
    return (
      <SectionedPage
        {...header}
        sections={[]}
        headerActions={
          <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>Refresh</Button>
        }
        banner={
          query.isError ? (
            <Alert
              type="error"
              showIcon
              message="The knowledge instances can't be listed"
              description={knowledgeErrorMessage(query.error)}
            />
          ) : (
            <NoInstances discovery={query.data?.discovery ?? 'ri'} />
          )
        }
      />
    )
  }

  const blocked = problem(selected)
  const sections: PageSection[] = blocked
    ? []
    : [
        // Keyed by instance: nothing selected or typed on one instance carries over to another.
        { ...KNOWLEDGE_BASE, content: <KnowledgeBaseSection key={selected.name} /> },
        { ...ONTOLOGY, content: <OntologySection key={selected.name} /> },
      ]
  const notices = [
    query.isError && (
      <Alert key="error" type="warning" showIcon message="The list of instances can't be refreshed" description={knowledgeErrorMessage(query.error)} />
    ),
    query.data?.stale && (
      <Alert
        key="stale"
        type="warning"
        showIcon
        message="Resource Inventory is unreachable: this is the last known list of instances."
      />
    ),
    blocked && <Alert key="blocked" type={selected.available && selected.capabilities ? 'info' : 'warning'} showIcon {...blocked} />,
    !blocked && (
      <div key="roles" style={{ fontSize: 12, color: colors.textMuted }}>
        <Space size={4} wrap>
          Your roles on this instance:
          {selected.capabilities?.roles.map((role) => <Tag key={role} style={{ marginInlineEnd: 0 }}>{role}</Tag>)}
        </Space>
      </div>
    ),
  ].filter(Boolean)

  return (
    <KnowledgeProvider info={selected}>
      <SectionedPage
        {...header}
        sections={sections}
        headerActions={
          <InstancePicker
            instances={instances}
            selected={selected}
            onSelect={(name) => {
              setWanted(name)
              saveInstance(name)
            }}
            onRefresh={() => query.refetch()}
            refreshing={query.isFetching}
          />
        }
        banner={<div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>{notices}</div>}
      />
    </KnowledgeProvider>
  )
}
