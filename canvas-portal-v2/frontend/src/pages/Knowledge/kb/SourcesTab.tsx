import React, { useState } from 'react'
import { Alert, Button, Input, List, Modal, Space, message } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { knowledgeErrorMessage, useStartIngestion, type Knowledge, type KnowledgeSourceKind } from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import { Muted } from '../shared'
import { SOURCE_LABELS, describeSource, isWritable } from './bases'

/** The sources of a knowledge-base, and their ingestion. */
export default function SourcesTab({ base, onStarted }: { base: Knowledge; onStarted: () => void }) {
  const access = useKnowledge()
  const ingest = useStartIngestion(access.instance)
  const [asking, setAsking] = useState<KnowledgeSourceKind>()
  const canIngest = isWritable(base) && access.can('createKnowledgeIngestion')
  const sources = base.source ?? []
  // Documents can always be uploaded and ingested, whether or not "upload" is listed as a source.
  const kinds = [...new Set<KnowledgeSourceKind>(['upload', ...sources.map((s) => s.kind)])]

  function start(source: KnowledgeSourceKind, params?: Record<string, unknown>) {
    ingest.mutate(
      { id: base.id, request: { source, ...(params ? { params } : {}) } },
      {
        onSuccess: () => {
          message.success('The ingestion started')
          setAsking(undefined)
          onStarted()
        },
        onError: (error) => source === 'upload' && message.error(knowledgeErrorMessage(error), 6),
      },
    )
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Muted>Event sources are also ingested as their events arrive. Documents are ingested on request.</Muted>
      <List
        bordered
        dataSource={kinds}
        renderItem={(kind) => (
          <List.Item
            actions={
              canIngest
                ? [
                    <Button
                      key="ingest"
                      icon={<PlayCircleOutlined />}
                      loading={ingest.isPending && ingest.variables?.request.source === kind}
                      onClick={() => {
                        ingest.reset()
                        if (kind === 'upload') start(kind)
                        else setAsking(kind)
                      }}
                    >
                      {kind === 'upload' ? 'Ingest pending documents' : 'Ingest'}
                    </Button>,
                  ]
                : undefined
            }
          >
            <List.Item.Meta
              title={SOURCE_LABELS[kind]}
              description={
                sources.filter((s) => s.kind === kind).map(describeSource).filter((text) => text !== SOURCE_LABELS[kind]).join(' · ') ||
                (kind === 'upload' ? 'Every document that has not been ingested yet' : 'Everything the source provides')
              }
            />
          </List.Item>
        )}
      />
      {!isWritable(base) && <Muted>A deprecated knowledge-base can't ingest.</Muted>}
      {asking && (
        <IngestModal
          kind={asking}
          pending={ingest.isPending}
          error={ingest.isError ? knowledgeErrorMessage(ingest.error) : undefined}
          onClose={() => setAsking(undefined)}
          onStart={(params) => start(asking, params)}
        />
      )}
    </Space>
  )
}

function IngestModal({
  kind,
  pending,
  error,
  onClose,
  onStart,
}: {
  kind: KnowledgeSourceKind
  pending: boolean
  error: string | undefined
  onClose: () => void
  onStart: (params: Record<string, unknown> | undefined) => void
}) {
  const [text, setText] = useState('')
  const [invalid, setInvalid] = useState<string>()

  function start() {
    setInvalid(undefined)
    if (!text.trim()) return onStart(undefined)
    let params: unknown
    try {
      params = JSON.parse(text)
    } catch {
      return setInvalid('The parameters are not valid JSON.')
    }
    if (typeof params !== 'object' || params === null || Array.isArray(params)) return setInvalid('The parameters must be a JSON object.')
    onStart(params as Record<string, unknown>)
  }

  return (
    <Modal open title={`Ingest from ${SOURCE_LABELS[kind]}`} onCancel={onClose} okText="Ingest" confirmLoading={pending} onOk={start}>
      <div style={{ marginBottom: 6 }}>Parameters (JSON, optional)</div>
      <Input.TextArea
        aria-label="Ingestion parameters"
        value={text}
        onChange={(e) => setText(e.target.value)}
        autoSize={{ minRows: 5, maxRows: 14 }}
        spellCheck={false}
        placeholder='{"resources": []}'
        style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
      />
      {(invalid ?? error) && <Alert style={{ marginTop: 12 }} type="error" showIcon message={invalid ?? error} />}
    </Modal>
  )
}
