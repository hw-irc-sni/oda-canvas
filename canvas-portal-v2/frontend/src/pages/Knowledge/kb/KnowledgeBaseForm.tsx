import React from 'react'
import { Alert, Button, Card, Col, Form, Input, InputNumber, Modal, Row, Select, Slider, Switch, message } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import {
  knowledgeErrorMessage,
  useCreateKnowledgeBase,
  useModules,
  usePatchKnowledgeBase,
  type Knowledge,
  type KnowledgeInput,
  type KnowledgePatch,
  type KnowledgeSource,
  type KnowledgeSourceKind,
} from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import { moduleName } from '../ontology/modules'
import { statusLabel } from '../shared'
import { CATEGORIES, SOURCE_KINDS, SOURCE_LABELS, STORE_KINDS, baseName, isEventSource } from './bases'

interface SourceFields {
  kind: KnowledgeSourceKind
  dependentApiName?: string
  eventTypes?: string[]
  query?: string
  url?: string
}

interface BaseFields extends Omit<KnowledgeInput, 'conformsTo' | 'source' | 'relatedParty' | 'validFor'> {
  conformsTo?: string[]
  source?: SourceFields[]
}

// The component's defaults, shown for a knowledge-base that doesn't set them.
const DEFAULT_RETRIEVAL = { topK: 8, scoreThreshold: 0, hybrid: true }

const text = (value: string | undefined) => value?.trim() ?? ''

function toSource(fields: SourceFields): KnowledgeSource {
  const source: KnowledgeSource = { kind: fields.kind }
  if (isEventSource(fields.kind)) {
    if (text(fields.dependentApiName)) source.dependentApiName = text(fields.dependentApiName)
    if (fields.eventTypes?.length) source.eventTypes = fields.eventTypes
    if (text(fields.query)) source.query = text(fields.query)
  }
  if (fields.kind === 'remote' && text(fields.url)) source.url = text(fields.url)
  return source
}

/** Creates a knowledge-base, or changes one. */
export default function KnowledgeBaseForm({ base, onClose }: { base?: Knowledge; onClose: () => void }) {
  const { instance, can } = useKnowledge()
  const [form] = Form.useForm<BaseFields>()
  const storeKind = Form.useWatch('storeKind', form) ?? base?.storeKind
  const modules = useModules(instance, { enabled: can('listKnowledgeSpecification') })
  const create = useCreateKnowledgeBase(instance)
  const patch = usePatchKnowledgeBase(instance)
  const error = create.error ?? patch.error
  const original = base?.conformsTo ?? []
  const options = (modules.data ?? [])
    .map((m) => ({ value: m.id, label: `${moduleName(m)} (${statusLabel(m.lifecycleStatus)})` }))
    .sort((a, b) => a.label.localeCompare(b.label))
  // A module that can't be listed (or not yet) is still shown and kept.
  for (const ref of original) {
    if (ref.id && !options.some((o) => o.value === ref.id)) options.push({ value: ref.id, label: ref.name ?? ref.id })
  }

  async function save() {
    const values = await form.validateFields().catch(() => null)
    if (!values) return
    const source = (values.source ?? []).map(toSource)
    const conformsTo = (values.conformsTo ?? []).map((id) => ({ id }))
    const name = values.name.trim()
    const done = (verb: string) => {
      message.success(`${name} was ${verb}`)
      onClose()
    }
    if (!base) {
      create.mutate(
        {
          name,
          description: text(values.description) || undefined,
          version: text(values.version) || undefined,
          knowledgeCategory: values.knowledgeCategory?.length ? values.knowledgeCategory : undefined,
          storeKind: values.storeKind,
          conformsTo: conformsTo.length ? conformsTo : undefined,
          source: source.length ? source : undefined,
          retrieval: values.storeKind === 'graph' ? undefined : values.retrieval,
        },
        { onSuccess: () => done('created as a draft') },
      )
      return
    }
    const next: KnowledgePatch = {
      name,
      description: text(values.description),
      version: text(values.version),
      knowledgeCategory: values.knowledgeCategory ?? [],
      conformsTo,
      source,
      ...(base.storeKind === 'graph' ? {} : { retrieval: values.retrieval }),
    }
    const before: Record<string, unknown> = {
      name: base.name,
      description: base.description ?? '',
      version: base.version ?? '',
      knowledgeCategory: base.knowledgeCategory ?? [],
      conformsTo: original.map(({ id }) => ({ id })),
      source: base.source ?? [],
      retrieval: { ...DEFAULT_RETRIEVAL, ...base.retrieval },
    }
    // Only what changed.
    const changes = Object.fromEntries(Object.entries(next).filter(([field, value]) => JSON.stringify(value) !== JSON.stringify(before[field])))
    if (Object.keys(changes).length === 0) return onClose()
    patch.mutate({ id: base.id, patch: changes }, { onSuccess: () => done('updated') })
  }

  return (
    <Modal
      open
      title={base ? `Edit ${baseName(base)}` : 'New knowledge-base'}
      width={780}
      onCancel={onClose}
      onOk={save}
      okText={base ? 'Save' : 'Create'}
      confirmLoading={create.isPending || patch.isPending}
      maskClosable={false}
    >
      <Form<BaseFields>
        form={form}
        layout="vertical"
        initialValues={{
          name: base?.name,
          description: base?.description,
          version: base?.version,
          knowledgeCategory: base?.knowledgeCategory,
          storeKind: base?.storeKind ?? 'hybrid',
          conformsTo: original.map((ref) => ref.id).filter(Boolean),
          source: base ? base.source ?? [] : [{ kind: 'upload' }],
          retrieval: { ...DEFAULT_RETRIEVAL, ...base?.retrieval },
        }}
      >
        <Row gutter={12}>
          <Col span={18}>
            <Form.Item label="Name" name="name" rules={[{ required: true, whitespace: true, message: 'Name is required' }]}>
              <Input placeholder="Outage runbooks" />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item label="Version" name="version"><Input placeholder="1.0" /></Form.Item>
          </Col>
          <Col span={24}>
            <Form.Item label="Description" name="description"><Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} /></Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label="Categories" name="knowledgeCategory">
              <Select mode="multiple" allowClear options={CATEGORIES} placeholder="What kind of knowledge it holds" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label="Store" name="storeKind" tooltip="It can't be changed later.">
              <Select disabled={!!base} options={STORE_KINDS} />
            </Form.Item>
          </Col>
          <Col span={24}>
            <Form.Item
              label="Conforms to"
              name="conformsTo"
              tooltip="The ontology modules that type its content. Only published (or deprecated) modules are used by extraction and queries."
            >
              <Select mode="multiple" allowClear optionFilterProp="label" loading={modules.isLoading} options={options} placeholder="Ontology modules" />
            </Form.Item>
          </Col>
        </Row>

        <div style={{ marginBottom: 8 }}>Sources</div>
        <Form.List name="source">
          {(fields, { add, remove }) => (
            <>
              {fields.map((field) => (
                <SourceCard key={field.key} name={field.name} onRemove={() => remove(field.name)} />
              ))}
              <Button type="dashed" block icon={<PlusOutlined />} onClick={() => add({ kind: 'upload' })} style={{ marginBottom: 18 }}>
                Add a source
              </Button>
            </>
          )}
        </Form.List>

        {storeKind !== 'graph' && (
          <Row gutter={18}>
            <Col span={8}>
              <Form.Item label="Hits returned (topK)" name={['retrieval', 'topK']} rules={[{ required: true, message: 'Enter 1 to 100' }]}>
                <InputNumber min={1} max={100} precision={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={10}>
              <Form.Item label="Minimum score" name={['retrieval', 'scoreThreshold']}>
                <Slider min={0} max={1} step={0.05} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item label="Hybrid search" name={['retrieval', 'hybrid']} valuePropName="checked" tooltip="Fuses embedding and keyword (BM25) scores.">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
        )}
      </Form>
      {error && <Alert type="error" showIcon message={knowledgeErrorMessage(error)} />}
    </Modal>
  )
}

function SourceCard({ name, onRemove }: { name: number; onRemove: () => void }) {
  const kind = Form.useWatch(['source', name, 'kind']) as KnowledgeSourceKind | undefined
  return (
    <Card size="small" style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'start' }}>
        <Form.Item name={[name, 'kind']} style={{ flex: 1, marginBottom: kind === 'upload' ? 0 : 10 }}>
          <Select aria-label="Source kind" options={SOURCE_KINDS.map((value) => ({ value, label: SOURCE_LABELS[value] }))} />
        </Form.Item>
        <Button aria-label="Remove the source" icon={<DeleteOutlined />} onClick={onRemove} />
      </div>
      {isEventSource(kind) && (
        <Row gutter={10}>
          <Col span={8}>
            <Form.Item label="Dependent API" name={[name, 'dependentApiName']} tooltip="The name of the component's dependent API that provides the events." style={{ marginBottom: 0 }}>
              <Input />
            </Form.Item>
          </Col>
          <Col span={9}>
            <Form.Item label="Event types" name={[name, 'eventTypes']} tooltip="All events of the API when empty." style={{ marginBottom: 0 }}>
              <Select mode="tags" tokenSeparators={[',', ' ']} open={false} suffixIcon={null} placeholder="All" />
            </Form.Item>
          </Col>
          <Col span={7}>
            <Form.Item label="Filter" name={[name, 'query']} style={{ marginBottom: 0 }}><Input /></Form.Item>
          </Col>
        </Row>
      )}
      {kind === 'remote' && (
        <Form.Item
          name={[name, 'url']}
          label="URL"
          style={{ marginBottom: 0 }}
          rules={[
            { required: true, whitespace: true, message: 'The URL is required' },
            { pattern: /^\s*https?:\/\/[^\s<>"{}|^`\\]+\s*$/, message: 'Enter an http or https URL' },
          ]}
        >
          <Input placeholder="https://example.org/data.ttl" />
        </Form.Item>
      )}
    </Card>
  )
}
