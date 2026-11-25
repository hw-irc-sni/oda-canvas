import React, { useState } from 'react'
import { Alert, Progress, Space, Spin, Table, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { isRunning, useDocuments, useTasks, type Knowledge, type Process, type ProcessTask } from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import { Muted, QueryError, formatDate } from '../shared'
import { STATE_COLORS, STATE_LABELS, STEPS, type useBaseProcesses } from './jobs'

interface JobsTabProps {
  base: Knowledge
  jobs: ReturnType<typeof useBaseProcesses>
}

function StateTag({ state }: { state: Process['state'] }) {
  return state ? <Tag color={STATE_COLORS[state]}>{STATE_LABELS[state]}</Tag> : <Muted>—</Muted>
}

/** The TMF701 processes of the knowledge-base, with their tasks. */
export default function JobsTab({ base, jobs }: JobsTabProps) {
  const { can } = useKnowledge()
  const { processes, query } = jobs
  // A running process is open unless it was closed; the others are closed unless they were opened.
  const [toggled, setToggled] = useState<Record<string, boolean>>({})
  const columns: ColumnsType<Process> = [
    { title: 'Started', dataIndex: 'creationDate', key: 'creationDate', width: 175, render: (value?: string) => formatDate(value) },
    { title: 'Ingestion', dataIndex: 'description', key: 'description', render: (value: string | undefined, p) => value ?? p.id },
    { title: 'State', key: 'state', width: 110, render: (_, p) => <StateTag state={p.state} /> },
    { title: 'Finished', dataIndex: 'completionDate', key: 'completionDate', width: 175, render: (value?: string) => formatDate(value) },
  ]
  if (query.isLoading) return <Spin />
  if (query.isError && !query.data) return <QueryError error={query.error} what="the jobs" onRetry={() => query.refetch()} />
  return (
    <Table<Process>
      rowKey="id"
      size="small"
      columns={columns}
      dataSource={processes}
      pagination={{ pageSize: 10, hideOnSinglePage: true }}
      locale={{ emptyText: 'Nothing has been ingested yet.' }}
      expandable={{
        expandedRowKeys: processes.filter((p) => toggled[p.id] ?? isRunning(p.state)).map((p) => p.id),
        onExpand: (expanded, p) => setToggled((current) => ({ ...current, [p.id]: expanded })),
        rowExpandable: () => can('listTask'),
        expandedRowRender: (process) => <Tasks base={base} process={process} />,
      }}
    />
  )
}

/** The steps of each document (or source batch) of a process. */
function Tasks({ base, process }: { base: Knowledge; process: Process }) {
  const { instance, can } = useKnowledge()
  const query = useTasks(instance, process.id, { polling: isRunning(process.state) })
  const documents = useDocuments(instance, base.id, { enabled: can('listKnowledgeDocument') })
  if (query.isLoading) return <Spin />
  if (query.isError && !query.data) return <QueryError error={query.error} what="the tasks" onRetry={() => query.refetch()} />
  const tasks = query.data ?? []
  const chains = new Map<string, ProcessTask[]>()
  for (const task of tasks) chains.set(task.documentId ?? '', [...(chains.get(task.documentId ?? '') ?? []), task])
  const finished = tasks.filter((task) => task.state === 'completed').length
  const failed = tasks.some((task) => task.state === 'failed')

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Progress
        percent={tasks.length ? Math.round((finished / tasks.length) * 100) : 0}
        status={failed ? 'exception' : isRunning(process.state) ? 'active' : undefined}
        format={() => `${finished}/${tasks.length}`}
      />
      {process.errorMessage && <Alert type="error" showIcon message={process.errorMessage} />}
      {[...chains.entries()].map(([documentId, chain]) => (
        <div key={documentId}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>
            {documentId ? documents.data?.find((d) => d.id === documentId)?.name ?? documentId : 'Source batch'}
          </div>
          <Space size={4} wrap>
            {STEPS.map(({ step, label }) => {
              const task = chain.find((t) => t.step === step)
              if (!task) return null
              const retried = (task.attempt ?? 0) > 1
              return (
                <Tooltip
                  key={step}
                  title={[
                    step,
                    task.state && STATE_LABELS[task.state],
                    retried && `attempt ${task.attempt}`,
                    task.errorMessage,
                  ].filter(Boolean).join(' · ')}
                >
                  <Tag color={task.state ? STATE_COLORS[task.state] : undefined} style={{ marginInlineEnd: 0 }}>
                    {label}
                    {retried && ` (attempt ${task.attempt})`}
                  </Tag>
                </Tooltip>
              )
            })}
          </Space>
          {/* The steps after a failed one are skipped, and say so: the first error is the one to read. */}
          {chain.filter((t) => t.errorMessage).sort((a, b) => (a.step ?? '').localeCompare(b.step ?? '')).slice(0, 1).map((t) => (
            <div key={t.id} style={{ marginTop: 4, fontSize: 12 }}>
              <Muted>{t.step}:</Muted> {t.errorMessage}
            </div>
          ))}
        </div>
      ))}
    </Space>
  )
}
