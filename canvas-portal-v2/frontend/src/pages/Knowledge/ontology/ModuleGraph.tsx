import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Collapse, Descriptions, Select, Space, Spin, Switch, Tag } from 'antd'
import type { Quad } from 'n3'
import { useModuleContent, useValidation, type KnowledgeSpecification, type ValidationReport } from '@/api/knowledge'
import KnowledgeGraph from '@/components/KnowledgeGraph'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { localName } from '../kb/sparql'
import { Muted, QueryError, formatDate } from '../shared'
import {
  MAX_NODES,
  drawContent,
  kindName,
  placeFindings,
  readContent,
  startsOnHierarchy,
  type ContentTerm,
  type Finding,
  type ModuleContent,
  type Statement,
} from './contentGraph'

/** The content of a module as a graph, whatever its lifecycle status, with what its last validation found. */
export default function ModuleGraph({ module }: { module: KnowledgeSpecification }) {
  const { instance, can } = useKnowledge()
  const query = useModuleContent(instance, module.id)
  const validation = useValidation(instance, module.id, { enabled: can('retrieveKnowledgeSpecificationValidation') })
  const parsed = useContent(query.data)

  if (query.isError && query.data === undefined) return <QueryError error={query.error} what="the content" onRetry={() => query.refetch()} />
  if (query.isLoading || (!parsed.content && !parsed.error)) return <div style={{ padding: 60, textAlign: 'center' }}><Spin /></div>
  if (parsed.error) return <Alert type="error" showIcon message="The content can't be drawn" description={parsed.error} />
  if (!query.data?.trim()) return <Muted>This module has no content yet.</Muted>
  return <ContentGraph module={module} content={parsed.content!} report={validation.data} />
}

/** Parses the Turtle; the parser is loaded when a graph is first shown. */
function useContent(turtle: string | undefined): { content?: ModuleContent; error?: string } {
  const [state, setState] = useState<{ turtle?: string; content?: ModuleContent; error?: string }>({})
  useEffect(() => {
    if (turtle === undefined) return
    let current = true
    import('n3')
      .then(({ Parser }) => {
        const quads: Quad[] = new Parser({ format: 'text/turtle' }).parse(turtle)
        if (current) setState({ turtle, content: readContent(quads) })
      })
      .catch((error) => {
        if (current) setState({ turtle, error: error instanceof Error ? error.message : String(error) })
      })
    return () => {
      current = false
    }
  }, [turtle])
  return state.turtle === turtle ? state : {}
}

interface ContentGraphProps {
  module: KnowledgeSpecification
  content: ModuleContent
  report: ValidationReport | null | undefined
}

function ContentGraph({ module, content, report }: ContentGraphProps) {
  const [relations, setRelations] = useState(() => !startsOnHierarchy(content))
  const [external, setExternal] = useState(true)
  const [selected, setSelected] = useState<string>()
  const [around, setAround] = useState<string>()
  const findings = useMemo(() => placeFindings(report, content), [report, content])
  // A term that a new version of the content no longer has.
  const focus = around && content.terms.has(around) ? around : undefined
  const graph = useMemo(
    () => drawContent(content, { relations, external, around: focus, findings: findings.byTerm }),
    [content, relations, external, focus, findings],
  )
  const options = useMemo(
    () =>
      [...content.terms.values()]
        .map((t) => ({ value: t.iri, label: `${t.label} (${kindName(t).toLowerCase()})` }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [content],
  )
  const term = selected ? content.terms.get(selected) : undefined

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <ModuleFindings module={module} content={content} report={report} findings={findings.onModule} flagged={[...findings.byTerm.keys()]} onOpen={setSelected} />
      <Space wrap size={12}>
        <Select
          showSearch
          allowClear
          aria-label="Find a term"
          placeholder="Find a term"
          value={term?.iri}
          onChange={setSelected}
          optionFilterProp="label"
          options={options}
          popupMatchSelectWidth={380}
          style={{ width: 240 }}
        />
        {!focus && <Toggle label="Relations" checked={relations} onChange={setRelations} />}
        {!focus && <Toggle label="Terms of other modules" checked={external} onChange={setExternal} />}
        {focus && <Button size="small" onClick={() => setAround(undefined)}>Show the whole module</Button>}
      </Space>
      {graph.cut > 0 && (
        <Alert
          type="info"
          showIcon
          message={`The ${MAX_NODES} most linked terms are drawn, and ${graph.cut} are left out. Find a term and show its neighbourhood to see the others.`}
        />
      )}
      <KnowledgeGraph
        nodes={graph.nodes}
        edges={graph.edges}
        selected={term?.iri}
        onSelect={(node) => setSelected(node.iri)}
        height={600}
        exportName={`${module.id}-content`}
        emptyText="The content declares no term that can be drawn"
      />
      <Muted>
        Dashed terms are referred to but not declared in this module; dashed links are OWL restrictions and class
        expressions. Attributes are in the details of their class.
      </Muted>
      {term && (
        <TermDetails
          term={term}
          content={content}
          findings={findings.byTerm.get(term.iri)}
          onOpen={setSelected}
          onAround={term.iri === focus ? undefined : () => setAround(term.iri)}
        />
      )}
      {content.loose.length > 0 && (
        <Collapse
          size="small"
          items={[
            {
              key: 'loose',
              label: `Properties that aren't links: no domain, or no class as range (${content.loose.length})`,
              children: (
                <Space size={[12, 4]} wrap>
                  {content.loose.map((iri) => (
                    <TermLink key={iri} iri={iri} text={content.terms.get(iri)?.label ?? localName(iri)} onOpen={setSelected} />
                  ))}
                </Space>
              ),
            },
          ]}
        />
      )}
    </Space>
  )
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <Space size={6}>
      <Switch size="small" aria-label={label} checked={checked} onChange={onChange} />
      <span style={{ fontSize: 12, color: colors.textSecondary }}>{label}</span>
    </Space>
  )
}

function TermLink({ iri, text, onOpen }: { iri: string; text: string; onOpen: (iri: string) => void }) {
  return (
    <Button type="link" size="small" title={iri} style={{ padding: 0, height: 'auto', whiteSpace: 'normal', textAlign: 'left' }} onClick={() => onOpen(iri)}>
      {text}
    </Button>
  )
}

/** The outcome of the last validation, with the findings that aren't about one term. */
function ModuleFindings({
  module,
  content,
  report,
  findings,
  flagged,
  onOpen,
}: {
  module: KnowledgeSpecification
  content: ModuleContent
  report: ValidationReport | null | undefined
  findings: Finding[]
  flagged: string[]
  onOpen: (iri: string) => void
}) {
  // Placeholder IRIs name no content (the component doesn't check them either).
  const mismatch = content.ontologies.length > 0 && !!module.iri && !module.iri.startsWith('urn:tmfc060:') && !content.ontologies.includes(module.iri)
  if (!report && !mismatch) return null
  if (report?.conforms && findings.length === 0 && flagged.length === 0 && !mismatch) return null
  const failed = report?.conforms === false
  return (
    <Alert
      type={failed ? 'error' : 'warning'}
      showIcon
      message={!report ? 'The IRI of the module' : failed ? 'The last validation failed' : 'The last validation passed, with remarks'}
      description={
        <div style={{ wordBreak: 'break-word' }}>
          {findings.map((finding, i) => (
            <div key={i}>
              <FindingTag finding={finding} /> {finding.text}
            </div>
          ))}
          {mismatch && (
            <div>
              The module is registered as <code>{module.iri}</code>, and its content declares{' '}
              {content.ontologies.map((iri, i) => (
                <React.Fragment key={iri}>
                  {i > 0 && ', '}
                  <code>{iri}</code>
                </React.Fragment>
              ))}
              .
            </div>
          )}
          {flagged.length > 0 && (
            <div>
              Marked in the graph:{' '}
              <Space size={[10, 0]} wrap>
                {flagged.map((iri) => (
                  <TermLink key={iri} iri={iri} text={content.terms.get(iri)?.label ?? localName(iri)} onOpen={onOpen} />
                ))}
              </Space>
            </div>
          )}
          {report && <Muted>Run {formatDate(report.runAt)}</Muted>}
        </div>
      }
    />
  )
}

function FindingTag({ finding }: { finding: Finding }) {
  return <Tag color={finding.severity === 'error' ? 'red' : 'orange'}>{finding.severity}</Tag>
}

function TermDetails({
  term,
  content,
  findings,
  onOpen,
  onAround,
}: {
  term: ContentTerm
  content: ModuleContent
  findings: Finding[] | undefined
  onOpen: (iri: string) => void
  onAround?: () => void
}) {
  const values = new Map<string, Statement[]>()
  for (const statement of term.statements) values.set(statement.predicate, [...(values.get(statement.predicate) ?? []), statement])
  const value = (statement: Statement, i: number) => (
    <div key={i} style={{ wordBreak: 'break-word' }}>
      {statement.iri && content.terms.has(statement.iri) ? (
        <TermLink iri={statement.iri} text={statement.text} onOpen={onOpen} />
      ) : (
        <span title={statement.iri} style={{ whiteSpace: 'pre-wrap' }}>{statement.text}</span>
      )}
    </div>
  )
  return (
    <div style={{ border: `1px solid ${colors.border}`, borderRadius: 12, padding: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <Space size={8} wrap>
          <span style={{ fontSize: 15, fontWeight: 650 }}>{term.label}</span>
          <Muted>{kindName(term)}</Muted>
        </Space>
        {onAround && <Button size="small" onClick={onAround}>Show only its neighbourhood</Button>}
      </div>
      <div style={{ fontSize: 12, color: colors.textMuted, wordBreak: 'break-all', marginBottom: 8 }}>{term.iri}</div>
      {findings?.map((finding, i) => (
        <div key={i} style={{ marginBottom: 6, wordBreak: 'break-word' }}>
          <FindingTag finding={finding} /> {finding.text}
        </div>
      ))}
      {term.definition && <p style={{ margin: '0 0 10px', whiteSpace: 'pre-wrap' }}>{term.definition}</p>}
      {values.size + term.properties.length === 0 ? (
        <Muted>The module states nothing about it.</Muted>
      ) : (
        <Descriptions column={1} size="small" bordered labelStyle={{ width: 170 }}>
          {term.properties.length > 0 && <Descriptions.Item label="Properties">{term.properties.map(value)}</Descriptions.Item>}
          {[...values.entries()].map(([predicate, statements]) => (
            <Descriptions.Item key={predicate} label={<span title={predicate}>{localName(predicate)}</span>}>
              {statements.map(value)}
            </Descriptions.Item>
          ))}
        </Descriptions>
      )}
    </div>
  )
}
