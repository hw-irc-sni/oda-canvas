import React, { useState } from 'react'
import { Alert, Button, Empty, Form, Input, Modal, Popconfirm, Radio, Space, Spin, Table, Tag, Tooltip, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { AuditOutlined, DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  knowledgeErrorMessage,
  useCreateModule,
  useDeleteModule,
  useModules,
  usePatchModule,
  type KnowledgeSpecification,
} from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { Muted, NeutralTag, QueryError, StatusTag } from '../shared'
import { moduleName } from './modules'
import { modulePermissions } from './permissions'

function endpoint(module: KnowledgeSpecification) {
  return module.source?.kind === 'remoteEndpoint' ? module.source : undefined
}

/** The remote modules: SPARQL endpoints and peer TMFC060 knowledge-bases that queries may reach. */
export default function FederationPeers({ onReview }: { onReview?: (id: string) => void }) {
  const access = useKnowledge()
  const query = useModules(access.instance)
  const remove = useDeleteModule(access.instance)
  const [editing, setEditing] = useState<KnowledgeSpecification | 'new'>()
  const peers = (query.data ?? []).filter((m) => m.specKind === 'remote')

  if (query.isLoading) return <div style={{ padding: 60, textAlign: 'center' }}><Spin /></div>
  if (query.isError && !query.data) return <QueryError error={query.error} what="the peers" onRetry={() => query.refetch()} />

  const columns: ColumnsType<KnowledgeSpecification> = [
    {
      title: 'Peer',
      key: 'name',
      sorter: (a, b) => moduleName(a).localeCompare(moduleName(b)),
      defaultSortOrder: 'ascend',
      render: (_, m) => (
        <div>
          <div style={{ fontWeight: 600, color: colors.textPrimary }}>{moduleName(m)}</div>
          <div style={{ fontSize: 11, color: colors.textMuted }}>{m.description || m.iri}</div>
        </div>
      ),
    },
    {
      title: 'Type',
      key: 'type',
      width: 150,
      render: (_, m) => <NeutralTag>{endpoint(m)?.endpointKind === 'tmfc060' ? 'peer TMFC060' : 'SPARQL endpoint'}</NeutralTag>,
    },
    { title: 'Endpoint', key: 'url', render: (_, m) => <code style={{ fontSize: 12, wordBreak: 'break-all' }}>{endpoint(m)?.url ?? '—'}</code> },
    { title: 'Status', key: 'status', width: 120, render: (_, m) => <StatusTag status={m.lifecycleStatus} /> },
    {
      title: 'Queries',
      key: 'queries',
      width: 130,
      render: (_, m) =>
        m.lifecycleStatus === 'published' ? <Tag color="green">allowed</Tag> : (
          <Tooltip title="Only a published peer may be named in a SERVICE clause."><span><NeutralTag>not allowed</NeutralTag></span></Tooltip>
        ),
    },
    {
      title: '',
      key: 'actions',
      width: 120,
      render: (_, m) => {
        const allowed = modulePermissions(access, m)
        return (
          <Space size={4}>
            {onReview && (
              <Tooltip title="Review"><Button size="small" aria-label={`Review ${m.id}`} icon={<AuditOutlined />} onClick={() => onReview(m.id)} /></Tooltip>
            )}
            {allowed.edit && (
              <Tooltip title="Edit"><Button size="small" aria-label={`Edit ${m.id}`} icon={<EditOutlined />} onClick={() => setEditing(m)} /></Tooltip>
            )}
            {allowed.delete && (
              <Popconfirm
                title={`Remove ${moduleName(m)}?`}
                description="Queries can no longer reach it."
                okText="Remove"
                okButtonProps={{ danger: true }}
                onConfirm={() =>
                  remove.mutateAsync(m.id).then(
                    () => message.success(`${moduleName(m)} was removed`),
                    (error) => message.error(knowledgeErrorMessage(error), 6),
                  )
                }
              >
                <Button size="small" danger aria-label={`Remove ${m.id}`} icon={<DeleteOutlined />} />
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
        <Muted>A peer is a remote module: once reviewed and published, knowledge-base queries may reach it with SERVICE.</Muted>
        <Space size={8}>
          <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>Refresh</Button>
          {access.can('createKnowledgeSpecification') && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>Register peer</Button>
          )}
        </Space>
      </div>
      <Table<KnowledgeSpecification>
        rowKey="id"
        size="middle"
        columns={columns}
        dataSource={peers}
        scroll={{ x: 900 }}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No federation peer is registered" /> }}
      />
      {editing && <PeerModal peer={editing === 'new' ? undefined : editing} onClose={() => setEditing(undefined)} />}
    </div>
  )
}

interface PeerFields {
  name: string
  endpointKind: 'sparql' | 'tmfc060'
  url: string
  iri?: string
  description?: string
}

function PeerModal({ peer, onClose }: { peer?: KnowledgeSpecification; onClose: () => void }) {
  const { instance } = useKnowledge()
  const [form] = Form.useForm<PeerFields>()
  const kind = Form.useWatch('endpointKind', form)
  const create = useCreateModule(instance)
  const patch = usePatchModule(instance)
  const error = create.error ?? patch.error
  const before = peer && endpoint(peer)

  async function save() {
    const values = await form.validateFields().catch(() => null)
    if (!values) return
    const url = values.url.trim()
    const source = { kind: 'remoteEndpoint' as const, url, endpointKind: values.endpointKind }
    const name = values.name.trim()
    const description = values.description?.trim() ?? ''
    const done = (verb: string) => {
      message.success(`${name} was ${verb}`)
      onClose()
    }
    if (peer) {
      patch.mutate({ id: peer.id, patch: { name, description, source } }, { onSuccess: () => done('updated') })
    } else {
      create.mutate(
        { name, iri: values.iri?.trim() || url, specKind: 'remote', description: description || undefined, source },
        { onSuccess: () => done('registered as a draft') },
      )
    }
  }

  return (
    <Modal
      open
      title={peer ? `Edit ${moduleName(peer)}` : 'Register a federation peer'}
      width={640}
      onCancel={onClose}
      onOk={save}
      okText={peer ? 'Save' : 'Register'}
      confirmLoading={create.isPending || patch.isPending}
    >
      <Form<PeerFields>
        form={form}
        layout="vertical"
        initialValues={{
          name: peer?.name,
          endpointKind: before?.endpointKind ?? 'sparql',
          url: before?.url,
          iri: peer?.iri,
          description: peer?.description,
        }}
      >
        <Form.Item label="Type" name="endpointKind">
          <Radio.Group
            optionType="button"
            options={[{ value: 'sparql', label: 'SPARQL endpoint' }, { value: 'tmfc060', label: 'Peer TMFC060 knowledge-base' }]}
          />
        </Form.Item>
        <Form.Item label="Name" name="name" rules={[{ required: true, whitespace: true, message: 'The name is required' }]}>
          <Input placeholder="Partner operator" />
        </Form.Item>
        <Form.Item
          label={kind === 'tmfc060' ? 'Query URL of the knowledge-base' : 'Endpoint URL'}
          extra="If the peer needs credentials, they are configured in the component's deployment, not here."
          name="url"
          rules={[
            { required: true, whitespace: true, message: 'The URL is required' },
            { pattern: /^\s*https?:\/\/[^\s<>"{}|^`\\]+\s*$/, message: 'Enter an http or https URL' },
            {
              // A URL with credentials would be stored and shown to every reader.
              validator: (_, value?: string) =>
                /^\s*https?:\/\/[^/?#]*@/.test(value ?? '') ? Promise.reject(new Error("Don't put credentials in the URL")) : Promise.resolve(),
            },
          ]}
        >
          <Input placeholder={kind === 'tmfc060' ? 'https://peer.example.org/…/knowledge/{id}/query' : 'https://peer.example.org/sparql'} />
        </Form.Item>
        <Form.Item
          label="IRI"
          name="iri"
          tooltip="Identifies the remote module. The endpoint URL, when empty."
          rules={[{ pattern: /^\s*([A-Za-z][A-Za-z0-9+.-]*:[^\s<>"{}|^`\\]+)?\s*$/, message: 'Enter an absolute IRI' }]}
        >
          <Input disabled={!!peer} placeholder="The endpoint URL" />
        </Form.Item>
        <Form.Item label="Description" name="description"><Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} /></Form.Item>
      </Form>
      {error && <Alert type="error" showIcon message={knowledgeErrorMessage(error)} />}
    </Modal>
  )
}
