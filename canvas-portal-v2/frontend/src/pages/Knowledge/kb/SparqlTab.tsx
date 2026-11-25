import React, { useState } from 'react'
import { Alert, Button, Input, InputNumber, Select, Space, Table, Tag, Tooltip } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { knowledgeErrorMessage, useQueryKnowledge, type Knowledge, type SparqlResults, type SparqlTerm } from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { CodeBlock, Muted } from '../shared'
import { termDetail, termText } from './sparql'

const EXAMPLES: { value: string; label: string }[] = [
  { label: 'Some triples', value: 'SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 25' },
  {
    label: 'Instances per class',
    value: 'SELECT ?class (COUNT(?s) AS ?instances)\nWHERE { ?s a ?class }\nGROUP BY ?class ORDER BY DESC(?instances)',
  },
  { label: 'A description as RDF', value: 'CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o } LIMIT 25' },
  { label: 'Is there anything?', value: 'ASK { ?s ?p ?o }' },
]

/** A console for read-only SPARQL over the knowledge-base and the modules it conforms to. */
export default function SparqlTab({ base }: { base: Knowledge }) {
  const { instance } = useKnowledge()
  const run = useQueryKnowledge(instance)
  const [query, setQuery] = useState(EXAMPLES[0].value)
  const [seconds, setSeconds] = useState<number | null>(30)
  const answer = run.data

  function submit() {
    if (query.trim()) run.mutate({ id: base.id, query, timeout: seconds ?? undefined })
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Muted>
        SELECT, ASK, CONSTRUCT or DESCRIBE over the graph and the published modules it conforms to. SERVICE may name published peers only.
      </Muted>
      <Input.TextArea
        aria-label="SPARQL query"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault()
            submit()
          }
        }}
        autoSize={{ minRows: 6, maxRows: 18 }}
        spellCheck={false}
        style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
      />
      <Space wrap size={12}>
        <Tooltip title="Ctrl+Enter">
          <Button type="primary" icon={<PlayCircleOutlined />} loading={run.isPending} disabled={!query.trim()} onClick={submit}>Run</Button>
        </Tooltip>
        <Space size={6}>
          <span style={{ fontSize: 12, color: colors.textSecondary }}>Timeout (s)</span>
          <InputNumber aria-label="Timeout" min={1} precision={0} value={seconds} onChange={setSeconds} style={{ width: 80 }} />
        </Space>
        <Select
          aria-label="Example queries"
          placeholder="Examples"
          value={null}
          onChange={(value: string | null) => value && setQuery(value)}
          options={EXAMPLES}
          style={{ width: 190 }}
        />
      </Space>
      {run.isError && <Alert type="error" showIcon message={knowledgeErrorMessage(run.error)} />}
      {answer && !run.isError && (
        <>
          {answer.truncated && <Alert type="warning" showIcon message="The server's row limit cut the result: only the first rows are shown." />}
          {answer.kind === 'graph' ? (
            answer.turtle.trim() ? <CodeBlock text={answer.turtle} filename={`${base.id}-query.ttl`} /> : <Muted>An empty graph.</Muted>
          ) : answer.results.boolean !== undefined ? (
            <Tag color={answer.results.boolean ? 'green' : 'red'}>{answer.results.boolean ? 'yes' : 'no'}</Tag>
          ) : (
            <ResultsTable results={answer.results} />
          )}
        </>
      )}
    </Space>
  )
}

function ResultsTable({ results }: { results: SparqlResults }) {
  const rows = (results.results?.bindings ?? []).map((binding, key) => ({ key, binding }))
  return (
    <Table
      size="small"
      dataSource={rows}
      scroll={{ x: 'max-content' }}
      pagination={{ pageSize: 25, hideOnSinglePage: true, showTotal: (total) => `${total} rows` }}
      locale={{ emptyText: 'No result.' }}
      columns={(results.head?.vars ?? []).map((name) => ({
        title: `?${name}`,
        key: name,
        sorter: (a: (typeof rows)[number], b: (typeof rows)[number]) =>
          (a.binding[name]?.value ?? '').localeCompare(b.binding[name]?.value ?? '', undefined, { numeric: true }),
        render: (_: unknown, row: (typeof rows)[number]) => <Value term={row.binding[name]} />,
      }))}
    />
  )
}

function Value({ term }: { term: SparqlTerm | undefined }) {
  if (!term) return <Muted>—</Muted>
  const detail = termDetail(term)
  return (
    <span style={{ wordBreak: 'break-word', color: term.type === 'uri' ? colors.primaryHover : undefined }}>
      {termText(term)} {detail && <Muted>{detail}</Muted>}
    </span>
  )
}
