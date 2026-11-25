import React, { useState } from 'react'
import { Alert, Button, Form, Modal, Popconfirm, Space, message } from 'antd'
import { DeleteOutlined, EditOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import {
  knowledgeErrorMessage,
  useDeleteModule,
  usePatchModule,
  useReplaceModuleContent,
  type KnowledgeSpecification,
  type KnowledgeSpecificationPatch,
} from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import ModuleFormFields, { toFields, toInput, type ModuleFields } from './ModuleForm'
import { moduleName } from './modules'
import { modulePermissions } from './permissions'
import TurtleInput from './TurtleInput'

interface ModuleActionsProps {
  module: KnowledgeSpecification
  modules: KnowledgeSpecification[]
  onDeleted: () => void
}

/** Edit, replace content, reload from git and delete, as far as the user may now. */
export default function ModuleActions({ module, modules, onDeleted }: ModuleActionsProps) {
  const access = useKnowledge()
  const allowed = modulePermissions(access, module)
  const [dialog, setDialog] = useState<'edit' | 'content'>()
  const patch = usePatchModule(access.instance)
  const remove = useDeleteModule(access.instance)

  if (!allowed.edit && !allowed.replaceContent && !allowed.reload && !allowed.delete) return null
  return (
    <Space size={8} wrap>
      {allowed.edit && <Button size="small" icon={<EditOutlined />} onClick={() => setDialog('edit')}>Edit</Button>}
      {allowed.replaceContent && (
        <Button size="small" icon={<UploadOutlined />} onClick={() => setDialog('content')}>Replace content</Button>
      )}
      {allowed.reload && (
        <Button
          size="small"
          icon={<ReloadOutlined />}
          loading={patch.isPending}
          onClick={() =>
            patch.mutate(
              { id: module.id, patch: { source: module.source } },
              {
                onSuccess: () => message.success('The content was loaded again from git'),
                onError: (error) => message.error(knowledgeErrorMessage(error)),
              },
            )
          }
        >
          Reload from git
        </Button>
      )}
      {allowed.delete && (
        <Popconfirm
          title={`Delete ${moduleName(module)}?`}
          description="Its content, mappings and review history are deleted too."
          okText="Delete"
          okButtonProps={{ danger: true }}
          onConfirm={() =>
            remove.mutateAsync(module.id).then(
              () => {
                message.success(`${moduleName(module)} was deleted`)
                onDeleted()
              },
              (error) => message.error(knowledgeErrorMessage(error), 6),
            )
          }
        >
          <Button size="small" danger icon={<DeleteOutlined />} loading={remove.isPending}>Delete</Button>
        </Popconfirm>
      )}
      {dialog === 'edit' && <EditModal module={module} modules={modules} onClose={() => setDialog(undefined)} />}
      {dialog === 'content' && <ContentModal module={module} onClose={() => setDialog(undefined)} />}
    </Space>
  )
}

function EditModal({ module, modules, onClose }: { module: KnowledgeSpecification; modules: KnowledgeSpecification[]; onClose: () => void }) {
  const { instance } = useKnowledge()
  const [form] = Form.useForm<ModuleFields>()
  const patch = usePatchModule(instance)

  async function save() {
    const values = await form.validateFields().catch(() => null)
    if (!values) return
    const before = toInput(toFields(module, modules))
    const after = toInput(values)
    // Only what changed. The component takes no null in its merge patches, so a cleared text is sent empty.
    const changes: Record<string, unknown> = {}
    for (const field of Object.keys({ ...before, ...after }) as (keyof typeof after)[]) {
      if (field !== 'iri' && JSON.stringify(before[field]) !== JSON.stringify(after[field])) {
        changes[field] = after[field] ?? (field === 'imports' || field === 'describes' ? [] : '')
      }
    }
    if (Object.keys(changes).length === 0) return onClose()
    patch.mutate(
      { id: module.id, patch: changes as KnowledgeSpecificationPatch },
      {
        onSuccess: () => {
          message.success(`${moduleName(module)} was updated`)
          onClose()
        },
      },
    )
  }

  return (
    <Modal open title={`Edit ${moduleName(module)}`} width={820} onCancel={onClose} onOk={save} okText="Save" confirmLoading={patch.isPending}>
      <Form<ModuleFields> form={form} layout="vertical" initialValues={toFields(module, modules)}>
        <ModuleFormFields form={form} modules={modules} editing={module} />
      </Form>
      {patch.isError && <Alert type="error" showIcon message={knowledgeErrorMessage(patch.error)} />}
    </Modal>
  )
}

function ContentModal({ module, onClose }: { module: KnowledgeSpecification; onClose: () => void }) {
  const { instance } = useKnowledge()
  const [turtle, setTurtle] = useState('')
  const replace = useReplaceModuleContent(instance)
  return (
    <Modal
      open
      title={`Replace the content of ${moduleName(module)}`}
      width={760}
      onCancel={onClose}
      okText="Replace"
      okButtonProps={{ disabled: !turtle.trim() }}
      confirmLoading={replace.isPending}
      onOk={() =>
        replace.mutate(
          { id: module.id, turtle },
          {
            onSuccess: () => {
              message.success('The content was replaced')
              onClose()
            },
          },
        )
      }
    >
      <TurtleInput value={turtle} onChange={setTurtle} />
      {replace.isError && <Alert style={{ marginTop: 12 }} type="error" showIcon message={knowledgeErrorMessage(replace.error)} />}
    </Modal>
  )
}
