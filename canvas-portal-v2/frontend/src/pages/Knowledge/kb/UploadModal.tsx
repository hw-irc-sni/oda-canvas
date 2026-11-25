import React, { useState } from 'react'
import { Alert, Button, Form, Input, Modal, Progress, Upload, message } from 'antd'
import { DeleteOutlined, InboxOutlined } from '@ant-design/icons'
import { knowledgeErrorMessage, useUploadDocument, type Knowledge } from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { baseName } from './bases'

interface Item {
  key: number
  file: File
  percent: number
  state: 'waiting' | 'uploading' | 'done' | 'failed'
  error?: string
}

let nextKey = 0

export function formatSize(bytes: number | undefined): string {
  if (bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** Uploads documents one after the other; those that fail can be tried again. */
export default function UploadModal({ base, onClose }: { base: Knowledge; onClose: () => void }) {
  const { instance } = useKnowledge()
  const upload = useUploadDocument(instance)
  const [items, setItems] = useState<Item[]>([])
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const open = items.filter((item) => item.state !== 'done')
  const single = items.length === 1

  const change = (key: number, fields: Partial<Item>) =>
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...fields } : item)))

  async function start() {
    setBusy(true)
    let failed = 0
    for (const item of open) {
      change(item.key, { state: 'uploading', percent: 0, error: undefined })
      try {
        await upload.mutateAsync({
          id: base.id,
          upload: {
            file: item.file,
            name: single ? name.trim() || undefined : undefined,
            description: single ? description.trim() || undefined : undefined,
            onProgress: (percent) => change(item.key, { percent }),
          },
        })
        change(item.key, { state: 'done', percent: 100 })
      } catch (error) {
        failed += 1
        change(item.key, { state: 'failed', error: knowledgeErrorMessage(error) })
      }
    }
    setBusy(false)
    if (failed === 0) {
      message.success(open.length === 1 ? 'The document was uploaded' : `${open.length} documents were uploaded`)
      onClose()
    }
  }

  return (
    <Modal
      open
      title={`Upload documents to ${baseName(base)}`}
      width={640}
      onCancel={onClose}
      maskClosable={false}
      footer={[
        <Button key="close" onClick={onClose} disabled={busy}>Close</Button>,
        <Button key="upload" type="primary" loading={busy} disabled={open.length === 0} onClick={start}>
          {!busy && items.some((item) => item.state === 'failed') ? 'Try again' : 'Upload'}
        </Button>,
      ]}
    >
      <Upload.Dragger
        multiple
        disabled={busy}
        showUploadList={false}
        accept=".pdf,.md,.markdown,.html,.htm,.ttl,.jsonld,.json"
        beforeUpload={(file) => {
          setItems((current) => [...current, { key: nextKey++, file, percent: 0, state: 'waiting' }])
          return false
        }}
      >
        <InboxOutlined style={{ fontSize: 26, color: colors.primary }} />
        <div>Choose or drop files</div>
        <div style={{ fontSize: 12, color: colors.textMuted }}>PDF, Markdown, HTML, Turtle, JSON-LD or JSON</div>
      </Upload.Dragger>
      {single && items[0].state !== 'done' && (
        <Form layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item label="Name" style={{ marginBottom: 10 }}>
            <Input aria-label="Document name" value={name} onChange={(e) => setName(e.target.value)} placeholder={items[0].file.name} />
          </Form.Item>
          <Form.Item label="Description" style={{ marginBottom: 0 }}>
            <Input aria-label="Document description" value={description} onChange={(e) => setDescription(e.target.value)} />
          </Form.Item>
        </Form>
      )}
      <div style={{ marginTop: 12 }}>
        {items.map((item) => (
          <div key={item.key} style={{ padding: '6px 0' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
              <span style={{ wordBreak: 'break-all' }}>{item.file.name}</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: colors.textMuted, whiteSpace: 'nowrap' }}>
                {formatSize(item.file.size)}
                {!busy && item.state !== 'done' && (
                  <Button
                    size="small"
                    type="text"
                    aria-label={`Remove ${item.file.name}`}
                    icon={<DeleteOutlined />}
                    onClick={() => setItems((current) => current.filter((i) => i.key !== item.key))}
                  />
                )}
              </span>
            </div>
            {item.state !== 'waiting' && (
              <Progress
                size="small"
                percent={item.percent}
                status={item.state === 'failed' ? 'exception' : item.state === 'done' ? 'success' : 'active'}
              />
            )}
            {item.error && <Alert type="error" message={item.error} style={{ marginTop: 4 }} />}
          </div>
        ))}
      </div>
    </Modal>
  )
}
