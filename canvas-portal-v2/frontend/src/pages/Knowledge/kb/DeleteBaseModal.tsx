import React, { useState } from 'react'
import { Alert, Input, Modal, message } from 'antd'
import { knowledgeErrorMessage, useDeleteKnowledgeBase, type Knowledge } from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import { baseName } from './bases'

/** Deletes a knowledge-base once its name is typed: its graph, vectors and documents go with it. */
export default function DeleteBaseModal({ base, onClose, onDeleted }: { base: Knowledge; onClose: () => void; onDeleted?: () => void }) {
  const { instance } = useKnowledge()
  const [typed, setTyped] = useState('')
  const remove = useDeleteKnowledgeBase(instance)
  const name = baseName(base)
  return (
    <Modal
      open
      title={`Delete ${name}`}
      onCancel={onClose}
      okText="Delete"
      okButtonProps={{ danger: true, disabled: typed !== name }}
      confirmLoading={remove.isPending}
      onOk={() =>
        remove.mutate(base.id, {
          onSuccess: () => {
            message.success(`${name} was deleted`)
            onClose()
            onDeleted?.()
          },
        })
      }
    >
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        message="Its graph, vectors, documents and ingestion history are deleted too. This can't be undone."
      />
      <div style={{ marginBottom: 6 }}>
        Type <code>{name}</code> to confirm:
      </div>
      <Input aria-label="Name of the knowledge-base" value={typed} onChange={(e) => setTyped(e.target.value)} />
      {remove.isError && <Alert style={{ marginTop: 12 }} type="error" showIcon message={knowledgeErrorMessage(remove.error)} />}
    </Modal>
  )
}
