import React from 'react'
import { Space, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { CheckCircleFilled, CloseCircleFilled, WarningFilled } from '@ant-design/icons'
import type { ShaclResult, ValidationReport, ValidationRule } from '@/api/knowledge'
import { colors } from '@/theme'
import { Muted, formatDate } from '../shared'

const RULES: Record<ValidationRule, { label: string; help: string }> = {
  parse: { label: 'Content parses', help: 'The content is valid Turtle.' },
  layerAnnotation: { label: 'Layer annotation', help: 'The declared layer matches the module, and no class is tagged deeper.' },
  layerMonotonicity: { label: 'Layer monotonicity', help: 'No class extends a class of a deeper layer.' },
  parentsResolve: { label: 'Parents resolve', help: 'Every parent class in a registered module exists there.' },
  catalogIntegrity: { label: 'Catalog integrity', help: 'The content declares the IRI of the module, and its imports resolve.' },
}

const SEVERITY_COLORS: Record<string, string> = { Violation: 'red', Warning: 'orange', Info: 'blue' }

function ConformsTag({ conforms }: { conforms: boolean | undefined }) {
  return conforms ? <Tag color="green">conforms</Tag> : <Tag color="red">doesn't conform</Tag>
}

/** SHACL results: the focus node, the path and the message of each. */
export function ShaclResults({ results, conforms }: { results: ShaclResult[] | undefined; conforms: boolean | undefined }) {
  const columns: ColumnsType<ShaclResult> = [
    {
      title: 'Severity',
      dataIndex: 'severity',
      key: 'severity',
      width: 110,
      render: (value?: string) => <Tag color={SEVERITY_COLORS[value ?? ''] ?? 'blue'}>{value ?? '—'}</Tag>,
    },
    { title: 'Focus node', dataIndex: 'focusNode', key: 'focusNode', render: iri },
    { title: 'Path', dataIndex: 'resultPath', key: 'resultPath', render: iri },
    { title: 'Message', dataIndex: 'message', key: 'message', render: (value?: string) => value ?? '—' },
    { title: 'Shape', dataIndex: 'sourceShape', key: 'sourceShape', render: iri },
  ]
  if (!results?.length) return <Muted>{conforms ? 'No SHACL result: nothing violates the shapes.' : 'No SHACL result.'}</Muted>
  return (
    <Table<ShaclResult>
      rowKey={(_, i) => String(i)}
      size="small"
      columns={columns}
      dataSource={results}
      scroll={{ x: 760 }}
      pagination={{ pageSize: 10, hideOnSinglePage: true }}
    />
  )
}

function iri(value?: string) {
  return value ? <span style={{ fontSize: 12, wordBreak: 'break-all' }}>{value}</span> : '—'
}

/** A module's validation: each structural rule, then SHACL. */
export default function ValidationReportView({ report }: { report: ValidationReport }) {
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space size={8} wrap>
        <ConformsTag conforms={report.conforms} />
        <Muted>Run {formatDate(report.runAt)}</Muted>
      </Space>
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 10, overflow: 'hidden' }}>
        {(report.rules ?? []).map((result, i) => {
          const warned = result.passed && (result.messages?.length ?? 0) > 0
          return (
            <div key={result.rule} style={{ padding: '10px 14px', borderTop: i ? `1px solid ${colors.borderLight}` : undefined }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
                {!result.passed ? (
                  <CloseCircleFilled aria-label="failed" style={{ color: colors.statusDown }} />
                ) : warned ? (
                  <WarningFilled aria-label="passed with warnings" style={{ color: colors.statusDeprecated }} />
                ) : (
                  <CheckCircleFilled aria-label="passed" style={{ color: colors.statusLive }} />
                )}
                <div>
                  <div style={{ fontWeight: 600 }}>{RULES[result.rule]?.label ?? result.rule}</div>
                  <div style={{ fontSize: 12, color: colors.textMuted }}>{RULES[result.rule]?.help}</div>
                  {result.messages?.map((message, j) => (
                    <div
                      key={j}
                      style={{
                        fontSize: 12,
                        wordBreak: 'break-word',
                        color: message.severity === 'error' ? colors.statusDown : colors.statusDeprecated,
                      }}
                    >
                      {message.text}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )
        })}
      </div>
      <div>
        <Space size={8} style={{ marginBottom: 8 }}>
          <span style={{ fontWeight: 600 }}>SHACL</span>
          <ConformsTag conforms={report.shacl?.conforms} />
        </Space>
        <ShaclResults results={report.shacl?.results} conforms={report.shacl?.conforms} />
      </div>
    </Space>
  )
}
