import React, { useState } from 'react'
import { Button, Popconfirm, Space, Table, Tag, Tooltip, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DeleteOutlined, DownloadOutlined, PlayCircleOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import {
  fetchDocumentContent,
  knowledgeErrorMessage,
  saveBlob,
  useDeleteDocument,
  useDocuments,
  useStartIngestion,
  type Knowledge,
  type KnowledgeDocument,
} from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { Muted, NeutralTag, QueryError, formatDate } from '../shared'
import { isWritable } from './bases'
import UploadModal, { formatSize } from './UploadModal'

function StateTag({ state }: { state: KnowledgeDocument['ingestionState'] }) {
  if (state === 'ingested') return <Tag color="green">ingested</Tag>
  if (state === 'failed') return <Tag color="red">failed</Tag>
  return <NeutralTag>{state ?? 'unknown'}</NeutralTag>
}

export default function DocumentsTab({ base, running }: { base: Knowledge; running: boolean }) {
  const access = useKnowledge()
  const query = useDocuments(access.instance, base.id, { polling: running })
  const remove = useDeleteDocument(access.instance)
  const ingest = useStartIngestion(access.instance)
  const [uploading, setUploading] = useState(false)
  const [chosen, setChosen] = useState<string[]>([])
  const [downloading, setDownloading] = useState<string>()
  const writable = isWritable(base)
  const canUpload = writable && access.can('createKnowledgeDocument')
  const canIngest = writable && access.can('createKnowledgeIngestion')
  const documents = query.data ?? []
  const selected = chosen.filter((id) => documents.some((d) => d.id === id))

  function start(documentId: string[]) {
    ingest.mutate(
      { id: base.id, request: { source: 'upload', documentId } },
      {
        onSuccess: () => {
          message.success('The ingestion started: follow it in Jobs')
          setChosen([])
        },
        onError: (error) => message.error(knowledgeErrorMessage(error), 6),
      },
    )
  }

  async function download(document: KnowledgeDocument) {
    setDownloading(document.id)
    try {
      saveBlob(await fetchDocumentContent(access.instance, base.id, document.id), document.name || document.id)
    } catch (error) {
      message.error(knowledgeErrorMessage(error), 6)
    } finally {
      setDownloading(undefined)
    }
  }

  const columns: ColumnsType<KnowledgeDocument> = [
    {
      title: 'Document',
      key: 'name',
      sorter: (a, b) => (a.name ?? '').localeCompare(b.name ?? ''),
      render: (_, d) => (
        <div>
          <div style={{ fontWeight: 600, wordBreak: 'break-word' }}>{d.name ?? d.id}</div>
          {d.description && <div style={{ fontSize: 11, color: colors.textMuted }}>{d.description}</div>}
        </div>
      ),
    },
    { title: 'Type', dataIndex: 'mediaType', key: 'mediaType', width: 130, render: (value?: string) => value ?? '—' },
    { title: 'Size', dataIndex: 'size', key: 'size', width: 90, render: (value?: number) => formatSize(value) },
    {
      title: 'Uploaded',
      dataIndex: 'uploadDate',
      key: 'uploadDate',
      width: 165,
      defaultSortOrder: 'descend',
      sorter: (a, b) => (a.uploadDate ?? '').localeCompare(b.uploadDate ?? ''),
      render: (value?: string) => formatDate(value),
    },
    { title: 'Ingestion', key: 'state', width: 105, render: (_, d) => <StateTag state={d.ingestionState} /> },
    {
      title: '',
      key: 'actions',
      width: 115,
      render: (_, d) => (
        <Space size={4}>
          {canIngest && (
            <Tooltip title="Ingest"><Button size="small" aria-label={`Ingest ${d.name}`} icon={<PlayCircleOutlined />} onClick={() => start([d.id])} /></Tooltip>
          )}
          {access.can('retrieveKnowledgeDocumentContent') && (
            <Tooltip title="Download">
              <Button size="small" aria-label={`Download ${d.name}`} icon={<DownloadOutlined />} loading={downloading === d.id} onClick={() => download(d)} />
            </Tooltip>
          )}
          {writable && access.can('deleteKnowledgeDocument') && (
            <Popconfirm
              title={`Delete ${d.name}?`}
              description="Its chunks go too; triples extracted from it stay in the graph."
              okText="Delete"
              okButtonProps={{ danger: true }}
              onConfirm={() =>
                remove.mutateAsync({ id: base.id, documentId: d.id }).then(
                  () => message.success('The document was deleted'),
                  (error) => message.error(knowledgeErrorMessage(error), 6),
                )
              }
            >
              <Button size="small" danger aria-label={`Delete ${d.name}`} icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  if (query.isError && !query.data) return <QueryError error={query.error} what="the documents" onRetry={() => query.refetch()} />
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        <Space size={8}>
          {canUpload && <Button type="primary" icon={<UploadOutlined />} onClick={() => setUploading(true)}>Upload</Button>}
          {canIngest && (
            <Button icon={<PlayCircleOutlined />} disabled={selected.length === 0} loading={ingest.isPending} onClick={() => start(selected)}>
              Ingest selected
            </Button>
          )}
          {!writable && <Muted>A deprecated knowledge-base can't be changed.</Muted>}
        </Space>
        <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>Refresh</Button>
      </div>
      <Table<KnowledgeDocument>
        rowKey="id"
        size="small"
        loading={query.isLoading}
        columns={columns}
        dataSource={documents}
        scroll={{ x: 760 }}
        rowSelection={canIngest ? { selectedRowKeys: selected, onChange: (keys) => setChosen(keys as string[]) } : undefined}
        pagination={{ pageSize: 15, hideOnSinglePage: true }}
        locale={{ emptyText: 'No document yet.' }}
      />
      {uploading && <UploadModal base={base} onClose={() => setUploading(false)} />}
    </div>
  )
}
