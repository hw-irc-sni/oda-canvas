import React, { useState } from 'react'
import { Alert, AutoComplete, Button, Empty, Form, Input, Modal, Popconfirm, Radio, Select, Space, Spin, Table, Tooltip, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SendOutlined } from '@ant-design/icons'
import {
  knowledgeErrorMessage,
  useCreateMapping,
  useCreateReview,
  useDeleteMapping,
  useMappings,
  useModules,
  usePatchMapping,
  type KnowledgeSpecification,
  type LifecycleStatus,
  type Mapping,
  type MappingInput,
} from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import { LIFECYCLE_STATUSES, Muted, NeutralTag, QueryError, StatusTag, statusLabel } from '../shared'
import { isFederated, moduleName } from './modules'
import { mayDelete } from './permissions'
import TermInput from './TermInput'
import { looksLikeTerm } from './TermExplorer'

const PREDICATES = [
  'skos:exactMatch',
  'skos:closeMatch',
  'skos:broadMatch',
  'skos:narrowMatch',
  'skos:relatedMatch',
  'rdfs:subClassOf',
  'rdfs:subPropertyOf',
  'owl:equivalentClass',
  'owl:equivalentProperty',
].map((value) => ({ value }))

/** The bridge statements of a bridge module, with their status and confidence. */
export default function MappingEditor() {
  const access = useKnowledge()
  const modules = useModules(access.instance)
  const bridges = (modules.data ?? []).filter((m) => m.specKind === 'bridge').sort((a, b) => moduleName(a).localeCompare(moduleName(b)))
  const [chosen, setChosen] = useState<string>()
  const bridge = bridges.find((m) => m.id === chosen) ?? bridges[0]

  if (modules.isLoading) return <div style={{ padding: 60, textAlign: 'center' }}><Spin /></div>
  if (modules.isError && !modules.data) return <QueryError error={modules.error} what="the modules" onRetry={() => modules.refetch()} />
  if (!bridge) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          access.can('createKnowledgeSpecification')
            ? 'No bridge module yet. Register a module of kind Bridge in the Modules tab to map terms between modules.'
            : 'No bridge module is registered on this instance.'
        }
      />
    )
  }
  return (
    <BridgeMappings
      key={bridge.id}
      bridge={bridge}
      picker={
        <Select
          aria-label="Bridge module"
          value={bridge.id}
          onChange={setChosen}
          style={{ minWidth: 260 }}
          popupMatchSelectWidth={false}
          options={bridges.map((m) => ({ value: m.id, label: `${moduleName(m)} (${statusLabel(m.lifecycleStatus)})` }))}
        />
      }
    />
  )
}

function BridgeMappings({ bridge, picker }: { bridge: KnowledgeSpecification; picker: React.ReactNode }) {
  const access = useKnowledge()
  const query = useMappings(access.instance, bridge.id)
  const [statuses, setStatuses] = useState<LifecycleStatus[]>([])
  const [editing, setEditing] = useState<Mapping | 'new'>()
  const remove = useDeleteMapping(access.instance)
  const federated = isFederated(bridge)
  const author = !federated && access.can('createKnowledgeSpecificationMapping')
  const shown = (query.data ?? []).filter((m) => statuses.length === 0 || (m.lifecycleStatus && statuses.includes(m.lifecycleStatus)))

  const columns: ColumnsType<Mapping> = [
    { title: 'Subject', dataIndex: 'subject', key: 'subject', render: term, sorter: (a, b) => (a.subject ?? '').localeCompare(b.subject ?? '') },
    { title: 'Predicate', dataIndex: 'predicate', key: 'predicate', render: term },
    { title: 'Object', dataIndex: 'object', key: 'object', render: term },
    { title: 'Status', key: 'lifecycleStatus', width: 110, render: (_, m) => <StatusTag status={m.lifecycleStatus} /> },
    {
      title: <Tooltip title="FTO's bridge:status, written to RDF">bridge:status</Tooltip>,
      dataIndex: 'status',
      key: 'status',
      width: 115,
      render: (value?: string) => (value ? <NeutralTag>{value}</NeutralTag> : <Muted>—</Muted>),
    },
    { title: 'Confidence', dataIndex: 'confidence', key: 'confidence', width: 105, render: (value?: string) => value ?? <Muted>—</Muted> },
    { title: 'Rationale', dataIndex: 'rationale', key: 'rationale', render: (value?: string) => value || <Muted>—</Muted> },
    {
      title: '',
      key: 'actions',
      width: 90,
      render: (_, m) => {
        const edit = author && access.can('patchKnowledgeSpecificationMapping') && m.lifecycleStatus !== 'deprecated'
        const del = !federated && mayDelete(access, 'deleteKnowledgeSpecificationMapping', m.lifecycleStatus)
        return (
          <Space size={4}>
            {edit && <Tooltip title="Edit"><Button size="small" aria-label={`Edit ${m.id}`} icon={<EditOutlined />} onClick={() => setEditing(m)} /></Tooltip>}
            {del && (
              <Popconfirm
                title="Delete this mapping?"
                okText="Delete"
                okButtonProps={{ danger: true }}
                onConfirm={() =>
                  remove.mutateAsync({ id: bridge.id, mappingId: m.id }).then(
                    () => message.success('The mapping was deleted'),
                    (error) => message.error(knowledgeErrorMessage(error), 6),
                  )
                }
              >
                <Button size="small" danger aria-label={`Delete ${m.id}`} icon={<DeleteOutlined />} />
              </Popconfirm>
            )}
          </Space>
        )
      },
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
        <Space wrap size={8}>
          {picker}
          <Select
            mode="multiple"
            allowClear
            placeholder="Statuses"
            aria-label="Mapping statuses"
            value={statuses}
            onChange={setStatuses}
            options={LIFECYCLE_STATUSES.map((value) => ({ value, label: statusLabel(value) }))}
            style={{ minWidth: 150 }}
            maxTagCount="responsive"
          />
        </Space>
        <Space size={8}>
          <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>Refresh</Button>
          {author && <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>New mapping</Button>}
        </Space>
      </div>
      {federated && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message="These mappings are governed by their federation repository and are read-only here." />
      )}
      {query.isError ? (
        <QueryError error={query.error} what="the mappings" onRetry={() => query.refetch()} />
      ) : (
        <Table<Mapping>
          rowKey="id"
          size="middle"
          loading={query.isLoading}
          columns={columns}
          dataSource={shown}
          scroll={{ x: 1000 }}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          locale={{ emptyText: query.data?.length ? 'No mapping has these statuses.' : 'This bridge has no mapping yet.' }}
        />
      )}
      {editing && (
        <MappingModal bridge={bridge} mapping={editing === 'new' ? undefined : editing} onClose={() => setEditing(undefined)} />
      )}
    </div>
  )
}

function term(value?: string) {
  return value ? <span style={{ wordBreak: 'break-all' }}>{value}</span> : <Muted>—</Muted>
}

interface MappingFields {
  subject: string
  predicate: string
  object: string
  confidence?: MappingInput['confidence']
  rationale?: string
  proposal?: string
}

const isTerm = { validator: (_: unknown, value?: string) =>
  !value?.trim() || looksLikeTerm(value.trim()) ? Promise.resolve() : Promise.reject(new Error('Enter an IRI or a CURIE (prefix:name)')) }

/** What the component does with an edit, by the mapping's state. */
function editNotice(status: LifecycleStatus | undefined): string | null {
  switch (status) {
    case 'agreed':
    case 'published':
      return `Editing ${status === 'agreed' ? 'an agreed' : 'a published'} mapping takes it back to draft: it needs a new review.`
    case 'proposed':
    case 'inReview':
      return 'This mapping is being reviewed. Your changes are applied to it and it stays in review.'
    case 'rejected':
      return 'Saving takes this rejected mapping back to draft; its review history is kept.'
    default:
      return null
  }
}

function MappingModal({ bridge, mapping, onClose }: { bridge: KnowledgeSpecification; mapping?: Mapping; onClose: () => void }) {
  const access = useKnowledge()
  const [form] = Form.useForm<MappingFields>()
  const create = useCreateMapping(access.instance)
  const patch = usePatchMapping(access.instance)
  const review = useCreateReview(access.instance)
  const [busy, setBusy] = useState<'save' | 'propose'>()
  const [failure, setFailure] = useState<string>()
  // The mapping as saved so far: if a later step fails, a retry changes it instead of creating another one.
  const [saved, setSaved] = useState<Mapping | undefined>(mapping)

  const status = saved?.lifecycleStatus
  const underReview = status === 'proposed' || status === 'inReview'
  const mayPropose = !underReview && access.canReview('proposal')
  const notice = editNotice(status)

  async function submit(propose: boolean) {
    const values = await form.validateFields().catch(() => null)
    if (!values) return
    const input: MappingInput = {
      subject: values.subject.trim(),
      predicate: values.predicate.trim(),
      object: values.object.trim(),
      confidence: values.confidence,
      rationale: values.rationale?.trim(),
    }
    setBusy(propose ? 'propose' : 'save')
    setFailure(undefined)
    let proposing = false
    try {
      let current = saved
      if (!current) {
        current = await create.mutateAsync({ id: bridge.id, input: { ...input, rationale: input.rationale || undefined } })
      } else {
        const before = current
        const changes = Object.fromEntries(
          (Object.keys(input) as (keyof MappingInput)[])
            .filter((field) => (input[field] ?? '') !== (before[field] ?? ''))
            .map((field) => [field, input[field]]),
        )
        if (Object.keys(changes).length > 0) {
          current = await patch.mutateAsync({ id: bridge.id, mappingId: current.id, patch: changes })
        }
      }
      setSaved(current)
      if (current.lifecycleStatus === 'rejected' && access.canTransition('knowledgeSpecification', 'rejected', 'draft')) {
        current = await patch.mutateAsync({ id: bridge.id, mappingId: current.id, patch: { lifecycleStatus: 'draft' } })
        setSaved(current)
      }
      if (propose) {
        if (current.lifecycleStatus !== 'draft') throw new Error(`${statusLabel(current.lifecycleStatus)} mappings can't be proposed: change something first.`)
        proposing = true
        await review.mutateAsync({
          id: bridge.id,
          input: { kind: 'proposal', target: { kind: 'mapping', id: current.id }, text: values.proposal?.trim() || undefined },
        })
      }
      message.success(propose ? 'The mapping was saved and proposed' : underReview ? 'The mapping was saved' : 'The mapping was saved as a draft')
      onClose()
    } catch (error) {
      const reason = knowledgeErrorMessage(error)
      setFailure(proposing ? `The mapping is saved, but wasn't proposed: ${reason}` : reason)
    } finally {
      setBusy(undefined)
    }
  }

  return (
    <Modal
      open
      title={mapping ? `Edit mapping ${mapping.id}` : `New mapping in ${moduleName(bridge)}`}
      width={680}
      onCancel={onClose}
      maskClosable={false}
      footer={[
        <Button key="cancel" onClick={onClose} disabled={!!busy}>Cancel</Button>,
        <Button key="save" type={mayPropose ? 'default' : 'primary'} loading={busy === 'save'} disabled={busy === 'propose'} onClick={() => submit(false)}>
          {underReview ? 'Save' : 'Save draft'}
        </Button>,
        mayPropose && (
          <Button key="propose" type="primary" icon={<SendOutlined />} loading={busy === 'propose'} disabled={busy === 'save'} onClick={() => submit(true)}>
            Save and propose
          </Button>
        ),
      ]}
    >
      {notice && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={notice} />}
      <Form<MappingFields>
        form={form}
        layout="vertical"
        initialValues={{
          subject: mapping?.subject,
          predicate: mapping?.predicate ?? 'skos:closeMatch',
          object: mapping?.object,
          confidence: mapping ? mapping.confidence : 'medium',
          rationale: mapping?.rationale,
        }}
      >
        <Form.Item label="Subject" name="subject" rules={[{ required: true, whitespace: true, message: 'The subject is required' }, isTerm]}>
          <TermInput placeholder="Type to search, or enter an IRI or CURIE" />
        </Form.Item>
        <Form.Item label="Predicate" name="predicate" rules={[{ required: true, whitespace: true, message: 'The predicate is required' }, isTerm]}>
          <AutoComplete
            options={PREDICATES}
            filterOption={(input, option) => !!option && option.value.toLowerCase().includes(input.toLowerCase())}
          />
        </Form.Item>
        <Form.Item label="Object" name="object" rules={[{ required: true, whitespace: true, message: 'The object is required' }, isTerm]}>
          <TermInput placeholder="Type to search, or enter an IRI or CURIE" />
        </Form.Item>
        <Form.Item label="Confidence" name="confidence">
          <Radio.Group optionType="button" options={['high', 'medium', 'low'].map((value) => ({ value, label: value }))} />
        </Form.Item>
        <Form.Item label="Rationale" name="rationale">
          <Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} placeholder="Why these terms correspond" />
        </Form.Item>
        {mayPropose && (
          <Form.Item label="Note for the reviewers" name="proposal" extra="Sent with Save and propose.">
            <Input.TextArea autoSize={{ minRows: 1, maxRows: 4 }} />
          </Form.Item>
        )}
      </Form>
      {failure && <Alert type="error" showIcon message={failure} />}
    </Modal>
  )
}
