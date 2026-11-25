import React, { useState } from 'react'
import { Alert, Button, Form, Input, Modal, Radio, Steps } from 'antd'
import {
  knowledgeErrorMessage,
  useCreateModule,
  useReplaceModuleContent,
  useRunValidation,
  type KnowledgeSpecification,
  type KnowledgeSpecificationSource,
  type ValidationReport,
} from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import ModuleFormFields, { toInput, type ModuleFields } from './ModuleForm'
import TurtleInput from './TurtleInput'
import ValidationReportView from './ValidationReportView'

type Step = 'register' | 'content' | 'validate'
type Outcome = { status: 'wait' | 'process' | 'finish' | 'error'; detail?: string }

interface UploadForm extends ModuleFields {
  sourceKind: 'upload' | 'git'
  content?: string
  gitUrl?: string
  gitRef?: string
  gitPath?: string
  gitSecret?: string
}

interface ModuleUploadProps {
  open: boolean
  modules: KnowledgeSpecification[]
  onClose: () => void
  onOpenModule: (id: string) => void
}

/** Registers a module from Turtle or from a git repository, then validates it. */
export default function ModuleUpload(props: ModuleUploadProps) {
  // Mounted per opening, so that each one starts empty.
  return props.open ? <UploadModal {...props} /> : null
}

function UploadModal({ modules, onClose, onOpenModule }: ModuleUploadProps) {
  const { instance, can } = useKnowledge()
  const [form] = Form.useForm<UploadForm>()
  // preserve: these fields are hidden after the registration, and their values are still needed.
  const sourceKind = Form.useWatch('sourceKind', { form, preserve: true }) ?? 'upload'
  const create = useCreateModule(instance)
  const replaceContent = useReplaceModuleContent(instance)
  const validate = useRunValidation(instance)
  const [busy, setBusy] = useState(false)
  // Once registered, a retry only redoes the steps after the registration.
  const [created, setCreated] = useState<KnowledgeSpecification>()
  const [outcomes, setOutcomes] = useState<Partial<Record<Step, Outcome>>>({})
  const [report, setReport] = useState<ValidationReport>()

  const steps: { key: Step; title: string }[] = [
    { key: 'register', title: sourceKind === 'git' ? 'Register and load from git' : 'Register the module' },
    ...(sourceKind === 'upload' ? [{ key: 'content' as Step, title: 'Upload the content' }] : []),
    { key: 'validate', title: 'Validate' },
  ]
  const started = Object.keys(outcomes).length > 0
  const contentDone = outcomes.content?.status === 'finish' || (sourceKind === 'git' && !!created)
  const finished = contentDone && (!!report || outcomes.validate?.status === 'error')

  async function run<T>(step: Step, action: () => Promise<T>): Promise<T | undefined> {
    setOutcomes((current) => ({ ...current, [step]: { status: 'process' } }))
    try {
      const result = await action()
      setOutcomes((current) => ({ ...current, [step]: { status: 'finish' } }))
      return result
    } catch (error) {
      setOutcomes((current) => ({ ...current, [step]: { status: 'error', detail: knowledgeErrorMessage(error) } }))
      return undefined
    }
  }

  async function submit() {
    const valid = await form.validateFields().catch(() => null)
    if (!valid) return
    const values: UploadForm = { ...form.getFieldsValue(true), ...valid }
    setBusy(true)
    try {
      let module = created
      if (!module) {
        const source: KnowledgeSpecificationSource =
          values.sourceKind === 'git'
            ? {
                kind: 'git',
                url: values.gitUrl!.trim(),
                path: values.gitPath!.trim(),
                ref: values.gitRef?.trim() || undefined,
                secretRef: values.gitSecret?.trim() || undefined,
              }
            : { kind: 'upload' }
        module = await run('register', () => create.mutateAsync({ ...toInput(values), source }))
        if (!module) return
        setCreated(module)
      }
      const { id } = module
      if (values.sourceKind === 'upload') {
        const uploaded = await run('content', async () => {
          await replaceContent.mutateAsync({ id, turtle: values.content ?? '' })
          return true
        })
        if (!uploaded) return
      }
      await check(id)
    } finally {
      setBusy(false)
    }
  }

  async function check(id: string) {
    if (!can('runKnowledgeSpecificationValidation')) return
    const result = await run('validate', () => validate.mutateAsync(id))
    setReport(result)
  }

  return (
    <Modal
      open
      title="Register an ontology module"
      width={820}
      onCancel={onClose}
      maskClosable={!started}
      footer={[
        <Button key="close" onClick={onClose} disabled={busy}>{created ? 'Close' : 'Cancel'}</Button>,
        created && (
          <Button key="open" onClick={() => onOpenModule(created.id)} disabled={busy}>Open the module</Button>
        ),
        finished ? (
          outcomes.validate?.status === 'error' && (
            <Button
              key="revalidate"
              type="primary"
              loading={busy}
              onClick={async () => {
                setBusy(true)
                await check(created!.id)
                setBusy(false)
              }}
            >
              Validate again
            </Button>
          )
        ) : (
          <Button key="submit" type="primary" loading={busy} onClick={submit}>
            {created ? 'Upload the content again' : 'Register'}
          </Button>
        ),
      ]}
    >
      <Form<UploadForm> form={form} layout="vertical" initialValues={{ specKind: 'ontology', sourceKind: 'upload' }} disabled={busy}>
        {!created && (
          <>
            <ModuleFormFields form={form} modules={modules} />
            <Form.Item label="Content" name="sourceKind" style={{ marginBottom: 10 }}>
              <Radio.Group optionType="button" options={[{ value: 'upload', label: 'Upload Turtle' }, { value: 'git', label: 'From a git repository' }]} />
            </Form.Item>
          </>
        )}
        {created && !finished && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={`${created.name ?? created.id} is registered as a draft, without content`}
            description="Correct the content and upload it again, or close: it can also be uploaded from the module's details."
          />
        )}
        {/* Kept mounted after the registration, so that the values stay in the form. */}
        {sourceKind === 'upload' ? (
          <div style={{ display: finished ? 'none' : undefined }}>
            <Form.Item name="content" rules={[{ required: true, whitespace: true, message: 'Choose a file or paste the Turtle' }]}>
              <TurtleInput />
            </Form.Item>
          </div>
        ) : (
          !created && (
            <>
              <Form.Item label="Repository URL" name="gitUrl" rules={[{ required: true, whitespace: true, message: 'The URL is required' }, { type: 'url', message: 'Enter a URL' }]}>
                <Input placeholder="https://github.com/example/ontology.git" />
              </Form.Item>
              <Form.Item label="Branch, tag or commit" name="gitRef"><Input placeholder="main" /></Form.Item>
              <Form.Item label="Path of the Turtle file" name="gitPath" rules={[{ required: true, whitespace: true, message: 'The path is required' }]}>
                <Input placeholder="telecom/network.ttl" />
              </Form.Item>
              <Form.Item
                label="Credential Secret"
                name="gitSecret"
                tooltip="For a private repository: the name of a Kubernetes Secret that the component's chart mounts (ontologySources[].credentialSecret). Never type a token here."
                rules={[{ pattern: /^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$/, message: 'Enter the name of a Secret, not its content' }]}
              >
                <Input placeholder="Name of a Secret, for a private repository" autoComplete="off" />
              </Form.Item>
            </>
          )
        )}
      </Form>

      {started && (
        <Steps
          direction="vertical"
          size="small"
          style={{ marginTop: 8 }}
          items={steps.map(({ key, title }) => ({
            title,
            status: outcomes[key]?.status ?? 'wait',
            description: outcomes[key]?.detail && <span style={{ wordBreak: 'break-word' }}>{outcomes[key]?.detail}</span>,
          }))}
        />
      )}
      {report && (
        <>
          <Alert
            style={{ marginBottom: 12 }}
            type={report.conforms ? 'success' : 'warning'}
            showIcon
            message={report.conforms ? 'The module is registered and passes validation' : "The module is registered, but doesn't pass validation"}
            description={report.conforms ? 'It is a draft: propose it to start its review.' : 'It stays a draft. It must pass before it can be agreed.'}
          />
          <ValidationReportView report={report} />
        </>
      )}
    </Modal>
  )
}
