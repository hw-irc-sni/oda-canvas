import React, { useState } from 'react'
import { Alert, Button, Empty, Radio, Select, Space, Spin, Tag, message } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import {
  knowledgeErrorMessage,
  useModules,
  useRunValidation,
  useValidateDocument,
  useValidation,
  type KnowledgeSpecification,
  type RdfMediaType,
} from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { Muted, QueryError, formatDate, statusLabel } from '../shared'
import { moduleName } from './modules'
import TurtleInput from './TurtleInput'
import ValidationReportView, { ShaclResults } from './ValidationReportView'

const byName = (a: KnowledgeSpecification, b: KnowledgeSpecification) => moduleName(a).localeCompare(moduleName(b))

const options = (modules: KnowledgeSpecification[]) =>
  modules.map((m) => ({ value: m.id, label: `${moduleName(m)} (${statusLabel(m.lifecycleStatus)})` }))

/** The validation reports of the modules, and the validation of a document against a shapes module. */
export default function ValidationPanel() {
  const access = useKnowledge()
  const modules = useModules(access.instance)
  if (modules.isLoading) return <div style={{ padding: 60, textAlign: 'center' }}><Spin /></div>
  if (modules.isError && !modules.data) return <QueryError error={modules.error} what="the modules" onRetry={() => modules.refetch()} />
  const all = [...(modules.data ?? [])].sort(byName)
  // The content of a remote module stays at its peer: there is nothing to check here.
  const checked = all.filter((m) => m.specKind !== 'remote')
  if (checked.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No ontology module is registered on this instance yet." />
  }
  return (
    <Space direction="vertical" size={28} style={{ width: '100%' }}>
      <ModuleValidation modules={checked} />
      {access.can('validateDocument') && <DocumentValidation shapes={all.filter((m) => m.specKind === 'shapes')} />}
    </Space>
  )
}

function ModuleValidation({ modules }: { modules: KnowledgeSpecification[] }) {
  const access = useKnowledge()
  const [chosen, setChosen] = useState<string>()
  const module = modules.find((m) => m.id === chosen)
  const query = useValidation(access.instance, module?.id ?? '', { enabled: !!module })
  const run = useRunValidation(access.instance)
  return (
    <section>
      <h4 style={{ margin: '0 0 10px' }}>Module validation</h4>
      <Space wrap size={8} style={{ marginBottom: 14 }}>
        <Select
          showSearch
          aria-label="Module to validate"
          placeholder="Select a module"
          value={module?.id}
          onChange={setChosen}
          optionFilterProp="label"
          options={options(modules)}
          style={{ minWidth: 300 }}
        />
        {module && access.can('runKnowledgeSpecificationValidation') && (
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={run.isPending}
            onClick={() => run.mutate(module.id, { onError: (error) => message.error(knowledgeErrorMessage(error), 6) })}
          >
            Run validation
          </Button>
        )}
      </Space>
      {!module ? (
        <div><Muted>The five structural rules and the SHACL shapes the module imports.</Muted></div>
      ) : query.isLoading ? (
        <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>
      ) : query.isError ? (
        <QueryError error={query.error} what="the validation" onRetry={() => query.refetch()} />
      ) : query.data ? (
        <ValidationReportView report={query.data} />
      ) : (
        <Muted>This module has not been validated yet.</Muted>
      )}
    </section>
  )
}

const FORMATS: { value: RdfMediaType; label: string }[] = [
  { value: 'text/turtle', label: 'Turtle' },
  { value: 'application/ld+json', label: 'JSON-LD' },
]

function DocumentValidation({ shapes }: { shapes: KnowledgeSpecification[] }) {
  const { instance } = useKnowledge()
  const [chosen, setChosen] = useState<string>()
  const [mediaType, setMediaType] = useState<RdfMediaType>('text/turtle')
  const [content, setContent] = useState('')
  const validate = useValidateDocument(instance)
  const id = shapes.some((m) => m.id === chosen) ? chosen : shapes[0]?.id
  const report = validate.data
  return (
    <section style={{ borderTop: `1px solid ${colors.border}`, paddingTop: 18 }}>
      <h4 style={{ margin: '0 0 4px' }}>Validate a document</h4>
      <p style={{ fontSize: 12, color: colors.textSecondary, margin: '0 0 12px' }}>
        Checks RDF data against a shapes module and the modules it imports. Nothing is stored.
      </p>
      {!id ? (
        <Muted>No shapes module is registered on this instance.</Muted>
      ) : (
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <Space wrap size={8}>
            <Select aria-label="Shapes module" value={id} onChange={setChosen} options={options(shapes)} style={{ minWidth: 300 }} />
            <Radio.Group
              optionType="button"
              options={FORMATS}
              value={mediaType}
              onChange={(e) => setMediaType(e.target.value as RdfMediaType)}
            />
          </Space>
          <TurtleInput
            value={content}
            onChange={setContent}
            label="Document"
            accept=".ttl,.turtle,.jsonld,.json,text/turtle,application/ld+json"
            placeholder={mediaType === 'text/turtle' ? '<https://example.org/a> a <https://example.org/ns#Thing> .' : '{"@id": "https://example.org/a"}'}
          />
          <Button
            type="primary"
            disabled={!content.trim()}
            loading={validate.isPending}
            onClick={() => validate.mutate({ id, content, mediaType })}
          >
            Validate
          </Button>
          {validate.isError && <Alert type="error" showIcon message={knowledgeErrorMessage(validate.error)} />}
          {report && !validate.isPending && (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space size={8}>
                {report.conforms ? <Tag color="green">conforms</Tag> : <Tag color="red">doesn't conform</Tag>}
                <Muted>{formatDate(report.runAt)}</Muted>
              </Space>
              <ShaclResults results={report.results} conforms={report.conforms} />
            </Space>
          )}
        </Space>
      )}
    </section>
  )
}
