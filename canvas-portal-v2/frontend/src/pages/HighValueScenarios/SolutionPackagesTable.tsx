import React from 'react'
import { Alert, Button, Table, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ExportOutlined, RightOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import type { SolutionPackageList, SolutionPackageStep } from '@/api/solutionPackages'
import { colors } from '@/theme'
import { markFor, type PlacedPackage } from './packagePlacement'

function errorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : 'The solution packages could not be loaded.'
}

function ScenarioCell({ placement }: { placement: PlacedPackage['placement'] }) {
  if (!placement.rowLabel) return <span style={{ color: colors.textMuted }}>—</span>
  const mark = markFor(placement)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span>
        <span style={{ color: colors.textMuted }}>{placement.stageLabel} › </span>
        {placement.rowLabel}
      </span>
      {mark && mark !== 'none' && (
        <span>
          <Tag color="red" style={{ marginInlineEnd: 0, fontSize: 11 }}>
            AN L4 high-value
          </Tag>
        </span>
      )}
    </div>
  )
}

function StepFlow({ steps }: { steps: SolutionPackageStep[] }) {
  return (
    <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {steps.map((step, index) => (
        <li key={step.name || index} style={{ display: 'grid', gridTemplateColumns: '72px 1fr', gap: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: colors.textSecondary, paddingTop: 6 }}>
            {step.name || `Step ${index + 1}`}
            {step.type && step.type !== 'AllSuccess' && (
              <div style={{ fontSize: 10, fontWeight: 400, color: colors.textMuted }}>{step.type}</div>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {step.tasks.map((task, i) => (
              <div
                key={i}
                style={{
                  border: `1px solid ${colors.borderLight}`,
                  background: colors.bgCard,
                  borderRadius: 8,
                  padding: '6px 10px',
                  fontSize: 12,
                }}
              >
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                  <Tag color="blue" style={{ marginInlineEnd: 0 }}>
                    {task.agent || 'Unassigned agent'}
                  </Tag>
                  {task.skill && <span style={{ color: colors.textSecondary }}>{task.skill}</span>}
                </div>
                <div style={{ color: colors.textPrimary, lineHeight: 1.5 }}>{task.description}</div>
              </div>
            ))}
            {step.next.length > 1 && (
              <div style={{ fontSize: 11, color: colors.textMuted }}>
                <RightOutlined style={{ fontSize: 9 }} /> {step.next.join(', ')}
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  )
}

const columns: ColumnsType<PlacedPackage> = [
  {
    title: 'Code',
    key: 'code',
    width: 96,
    sorter: (a, b) => (a.placement.code ?? '').localeCompare(b.placement.code ?? ''),
    defaultSortOrder: 'ascend',
    render: (_, { placement }) => (
      <span style={{ fontFamily: 'monospace', fontWeight: 600, whiteSpace: 'nowrap' }}>{placement.code ?? '—'}</span>
    ),
  },
  {
    title: 'Solution package',
    key: 'title',
    render: (_, { pkg, placement }) => (
      <div style={{ minWidth: 220 }}>
        <div style={{ fontWeight: 600, color: colors.textPrimary }}>{placement.title}</div>
        {pkg.summary && (
          <Tooltip title={pkg.summary} mouseEnterDelay={0.4}>
            <div
              style={{
                fontSize: 12,
                color: colors.textSecondary,
                lineHeight: 1.45,
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {pkg.summary}
            </div>
          </Tooltip>
        )}
      </div>
    ),
  },
  {
    title: 'Scenario',
    key: 'scenario',
    width: 210,
    render: (_, { placement }) => <ScenarioCell placement={placement} />,
  },
  {
    title: 'Domain',
    key: 'domain',
    width: 150,
    render: (_, { placement }) => placement.columnLabel ?? <span style={{ color: colors.textMuted }}>—</span>,
  },
  {
    title: 'Steps',
    key: 'steps',
    width: 72,
    align: 'right',
    sorter: (a, b) => a.pkg.steps.length - b.pkg.steps.length,
    render: (_, { pkg }) => pkg.steps.length,
  },
  {
    title: 'Agents',
    key: 'agents',
    width: 84,
    align: 'right',
    sorter: (a, b) => a.pkg.agents.length - b.pkg.agents.length,
    render: (_, { pkg }) =>
      pkg.agents.length ? (
        <Tooltip title={pkg.agents.join(', ')}>
          <span style={{ borderBottom: `1px dotted ${colors.textMuted}`, cursor: 'help' }}>{pkg.agents.length}</span>
        </Tooltip>
      ) : (
        0
      ),
  },
]

interface SolutionPackagesTableProps {
  data: SolutionPackageList | undefined
  placed: PlacedPackage[]
  loading: boolean
  error: unknown
  /** Describes the active map filter, if any. */
  filterLabel: string | null
  onClearFilter: () => void
}

export default function SolutionPackagesTable({
  data,
  placed,
  loading,
  error,
  filterLabel,
  onClearFilter,
}: SolutionPackagesTableProps) {
  if (data && !data.configured) {
    return (
      <div
        style={{
          border: `1px dashed ${colors.border}`,
          borderRadius: 10,
          padding: 14,
          background: colors.surfaceSubtle,
          fontSize: 12,
          color: colors.textMuted,
        }}
      >
        No OpenAN Orchestration Center is configured for this portal. Set <code>backend.orchestrationCenter</code> in
        the Helm values to list its solution packages.
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ fontSize: 12, color: colors.textSecondary }}>
          {data ? (
            <>
              {data.packages.length} solution package{data.packages.length === 1 ? '' : 's'} from the OpenAN
              Orchestration Center
              {data.fetchedAt && (
                <span style={{ color: colors.textMuted }}> · updated {dayjs(data.fetchedAt).format('HH:mm:ss')}</span>
              )}
            </>
          ) : (
            'Loading from the OpenAN Orchestration Center…'
          )}
        </div>
        {data?.pageUrl && (
          <Button size="small" icon={<ExportOutlined />} href={data.pageUrl} target="_blank" rel="noopener noreferrer">
            Workflow viewer
          </Button>
        )}
      </div>

      {error != null && <Alert type="error" showIcon message={errorMessage(error)} />}

      {filterLabel && (
        <Alert
          type="info"
          showIcon
          message={
            <span>
              Showing the packages for <strong>{filterLabel}</strong>.
            </span>
          }
          action={
            <Button size="small" type="link" onClick={onClearFilter}>
              Show all
            </Button>
          }
        />
      )}

      <Table<PlacedPackage>
        rowKey={(item) => item.pkg.id}
        size="small"
        columns={columns}
        dataSource={placed}
        loading={loading}
        pagination={false}
        scroll={{ x: 820 }}
        expandable={{
          expandedRowRender: ({ pkg }) => (
            <div style={{ padding: '4px 4px 4px 8px' }}>
              {pkg.summary && (
                <p style={{ fontSize: 12, color: colors.textSecondary, lineHeight: 1.55, margin: '0 0 12px' }}>
                  {pkg.summary}
                </p>
              )}
              <StepFlow steps={pkg.steps} />
            </div>
          ),
          rowExpandable: ({ pkg }) => pkg.steps.length > 0,
        }}
      />
    </div>
  )
}
