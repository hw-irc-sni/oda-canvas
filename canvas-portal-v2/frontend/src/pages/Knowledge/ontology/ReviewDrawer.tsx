import React, { useState } from 'react'
import { Alert, Button, Collapse, Descriptions, Drawer, Empty, Form, Input, Popconfirm, Radio, Space, Spin, Tabs, Tag, Timeline, message } from 'antd'
import {
  knowledgeErrorMessage,
  useCreateReview,
  usePatchMapping,
  usePatchModule,
  useReviews,
  useRunValidation,
  useTransitions,
  useValidation,
  type LinkStatus,
  type ReviewItem,
  type ReviewItemInput,
  type TransitionRecord,
} from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { LayerTag, LinkStatusTag, Muted, QueryError, StatusTag, formatDate, statusLabel } from '../shared'
import { moduleName } from './modules'
import { ACTION_LABELS, PATCH_ACTIONS, commentsOpen, reviewActions, type ReviewAction, type ReviewSubject } from './reviews'
import ValidationReportView from './ValidationReportView'

export default function ReviewDrawer({ subject, onClose }: { subject: ReviewSubject | undefined; onClose: () => void }) {
  return (
    <Drawer
      open={!!subject}
      onClose={onClose}
      width={760}
      title={
        subject && (
          <Space size={8} wrap>
            <span style={{ wordBreak: 'break-word' }}>{subject.title}</span>
            <StatusTag status={subject.status} />
          </Space>
        )
      }
    >
      {subject && <ReviewDetails key={subject.key} subject={subject} />}
    </Drawer>
  )
}

function ReviewDetails({ subject }: { subject: ReviewSubject }) {
  const access = useKnowledge()
  const { module, mapping, target } = subject
  const reviews = useReviews(access.instance, module.id)
  const history = useTransitions(access.instance, module.id, { enabled: access.can('listKnowledgeSpecificationTransition') })
  const mine = (item: { target?: { kind: string; id: string } }) => item.target?.kind === target.kind && item.target.id === target.id
  const thread = (reviews.data ?? []).filter(mine)
  const transitions = (history.data ?? []).filter(mine)

  return (
    <Space direction="vertical" size={18} style={{ width: '100%' }}>
      {mapping ? (
        <Descriptions column={1} size="small" bordered labelStyle={{ width: 120 }}>
          <Descriptions.Item label="Bridge">{moduleName(module)}</Descriptions.Item>
          <Descriptions.Item label="Subject"><span style={{ wordBreak: 'break-all' }}>{mapping.subject}</span></Descriptions.Item>
          <Descriptions.Item label="Predicate"><span style={{ wordBreak: 'break-all' }}>{mapping.predicate}</span></Descriptions.Item>
          <Descriptions.Item label="Object"><span style={{ wordBreak: 'break-all' }}>{mapping.object}</span></Descriptions.Item>
          <Descriptions.Item label="Confidence">{mapping.confidence ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Rationale">{mapping.rationale || '—'}</Descriptions.Item>
        </Descriptions>
      ) : (
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="IRI" span={2}><code style={{ wordBreak: 'break-all' }}>{module.iri}</code></Descriptions.Item>
          <Descriptions.Item label="Kind">{module.specKind ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Layer"><LayerTag layer={module.layer} /></Descriptions.Item>
          <Descriptions.Item label="Version">{module.version || '—'}</Descriptions.Item>
          <Descriptions.Item label="Link status"><LinkStatusTag status={module.linkStatus} /></Descriptions.Item>
        </Descriptions>
      )}

      <Actions subject={subject} />

      <Tabs
        items={[
          {
            key: 'thread',
            label: `Discussion (${thread.length})`,
            children: reviews.isError ? (
              <QueryError error={reviews.error} what="the review" onRetry={() => reviews.refetch()} />
            ) : reviews.isLoading ? (
              <Spin />
            ) : (
              <>
                <Thread items={thread} />
                <CommentBox subject={subject} />
              </>
            ),
          },
          ...(access.can('listKnowledgeSpecificationTransition')
            ? [
                {
                  key: 'history',
                  label: `History (${transitions.length})`,
                  children: history.isError ? (
                    <QueryError error={history.error} what="the history" onRetry={() => history.refetch()} />
                  ) : history.isLoading ? (
                    <Spin />
                  ) : (
                    <History items={transitions} reviews={thread} />
                  ),
                },
              ]
            : []),
          ...(access.can('retrieveKnowledgeSpecificationValidation')
            ? [{ key: 'validation', label: 'Validation', children: <Validation moduleId={module.id} /> }]
            : []),
        ]}
      />
    </Space>
  )
}

// ---- actions -------------------------------------------------------------------------------------------------

function useReviewCalls(subject: ReviewSubject) {
  const { instance } = useKnowledge()
  const review = useCreateReview(instance)
  const patchModule = usePatchModule(instance)
  const patchMapping = usePatchMapping(instance)
  const { module, mapping, target } = subject
  return {
    pending: review.isPending || patchModule.isPending || patchMapping.isPending,
    post: (input: Omit<ReviewItemInput, 'target'>) => review.mutateAsync({ id: module.id, input: { ...input, target } }),
    move: (action: ReviewAction) => {
      const lifecycleStatus = PATCH_ACTIONS[action]!
      return mapping
        ? patchMapping.mutateAsync({ id: module.id, mappingId: mapping.id, patch: { lifecycleStatus } })
        : patchModule.mutateAsync({ id: module.id, patch: { lifecycleStatus } })
    },
  }
}

const CONFIRM: Partial<Record<ReviewAction, string>> = {
  publish: 'It becomes part of the published ontology.',
  deprecate: "Deprecation can't be undone.",
}

const DONE: Partial<Record<ReviewAction, string>> = {
  start: 'The review started',
  publish: 'Published',
  deprecate: 'Deprecated',
  revise: 'Back to draft: the review history is kept',
}

function Actions({ subject }: { subject: ReviewSubject }) {
  const access = useKnowledge()
  const calls = useReviewCalls(subject)
  const [failure, setFailure] = useState<string>()
  const actions = reviewActions(access, subject.status)
  const moves = actions.filter((action) => action in PATCH_ACTIONS)

  async function run(work: () => Promise<unknown>, done: string): Promise<boolean> {
    setFailure(undefined)
    try {
      await work()
      message.success(done)
      return true
    } catch (error) {
      setFailure(knowledgeErrorMessage(error))
      return false
    }
  }

  const button = (action: ReviewAction, onClick?: () => void) => (
    <Button key={action} type="primary" danger={action === 'deprecate'} loading={calls.pending} onClick={onClick}>
      {ACTION_LABELS[action]}
    </Button>
  )
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {actions.filter((action) => action !== 'comment').length === 0 && <Muted>{waitingFor(subject)}</Muted>}
      {moves.length > 0 && (
        <Space size={8}>
          {moves.map((action) =>
            CONFIRM[action] ? (
              <Popconfirm
                key={action}
                title={`${ACTION_LABELS[action]}?`}
                description={CONFIRM[action]}
                okText={ACTION_LABELS[action]}
                onConfirm={() => run(() => calls.move(action), DONE[action]!)}
              >
                {button(action)}
              </Popconfirm>
            ) : (
              button(action, () => run(() => calls.move(action), DONE[action]!))
            ),
          )}
        </Space>
      )}
      {actions.includes('propose') && (
        <ProposeForm pending={calls.pending} onPropose={(text) => run(() => calls.post({ kind: 'proposal', text }), 'Proposed for review')} />
      )}
      {actions.includes('rule') && (
        <RulingForm
          subject={subject}
          pending={calls.pending}
          onRule={(ruling) =>
            run(() => calls.post({ kind: 'ruling', ...ruling }), ruling.ruling === 'agreed' ? 'Ruled agreed' : 'Ruled rejected')
          }
        />
      )}
      {failure && <Alert type="error" showIcon closable onClose={() => setFailure(undefined)} message={failure} />}
    </Space>
  )
}

/** Whose turn it is, when the user has nothing to do. */
function waitingFor({ status }: ReviewSubject): string {
  switch (status) {
    case 'draft':
      return 'A draft: an editor proposes it for review.'
    case 'proposed':
      return 'Proposed: a reviewer starts the review.'
    case 'inReview':
      return 'In review: an approver rules on it.'
    case 'agreed':
      return 'Agreed: an approver publishes it.'
    case 'rejected':
      return 'Rejected: an editor revises it.'
    case 'published':
      return 'Published.'
    case 'deprecated':
      return 'Deprecated.'
    default:
      return 'Its state is unknown.'
  }
}

function ProposeForm({ pending, onPropose }: { pending: boolean; onPropose: (text: string | undefined) => void }) {
  const [text, setText] = useState('')
  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Input.TextArea
        aria-label="Proposal note"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Note for the reviewers (optional)"
        autoSize={{ minRows: 2, maxRows: 5 }}
      />
      <Button type="primary" loading={pending} onClick={() => onPropose(text.trim() || undefined)}>Propose</Button>
    </Space>
  )
}

interface Ruling {
  ruling: 'agreed' | 'rejected'
  text?: string
  linkStatus?: LinkStatus
}

function RulingForm({ subject, pending, onRule }: { subject: ReviewSubject; pending: boolean; onRule: (ruling: Ruling) => Promise<boolean> }) {
  const access = useKnowledge()
  const [form] = Form.useForm<Ruling>()
  const ruling = Form.useWatch('ruling', form)
  const validation = useValidation(access.instance, subject.module.id, {
    enabled: access.can('retrieveKnowledgeSpecificationValidation'),
  })
  const may = (to: 'agreed' | 'rejected') => access.canTransition('knowledgeSpecification', subject.status, to)
  const of = subject.mapping ? 'its bridge module' : 'the module'
  return (
    <div style={{ border: `1px solid ${colors.border}`, borderRadius: 12, padding: 16, background: colors.surfaceSubtle }}>
      <div style={{ fontWeight: 650, marginBottom: 10 }}>Ruling</div>
      <Form<Ruling> form={form} layout="vertical" onFinish={(values) => onRule({ ...values, text: values.text?.trim() || undefined })}>
        <Form.Item name="ruling" rules={[{ required: true, message: 'Choose a ruling' }]}>
          <Radio.Group
            optionType="button"
            options={[
              { value: 'agreed', label: 'Agreed', disabled: !may('agreed') },
              { value: 'rejected', label: 'Rejected', disabled: !may('rejected') },
            ]}
          />
        </Form.Item>
        {ruling === 'agreed' && validation.data !== undefined && (
          <Alert
            style={{ marginBottom: 12 }}
            showIcon
            type={validation.data?.conforms ? 'success' : 'warning'}
            message={
              validation.data === null
                ? `No validation of ${of} has run yet. It runs with the ruling, and must pass.`
                : validation.data.conforms
                  ? `The last validation of ${of} passed. It runs again with the ruling.`
                  : `The last validation of ${of} failed: the ruling is refused until it passes.`
            }
          />
        )}
        {!subject.mapping && (
          <Form.Item label="Link status" name="linkStatus" tooltip="FTO's fto:linkStatus, written to the module with the ruling.">
            <Radio.Group
              optionType="button"
              options={[{ value: 'provisional', label: 'Provisional' }, { value: 'confirmed', label: 'Confirmed' }]}
            />
          </Form.Item>
        )}
        <Form.Item label="Rationale" name="text">
          <Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} />
        </Form.Item>
        <Button type="primary" htmlType="submit" danger={ruling === 'rejected'} loading={pending}>Record the ruling</Button>
      </Form>
    </div>
  )
}

function CommentBox({ subject }: { subject: ReviewSubject }) {
  const access = useKnowledge()
  const calls = useReviewCalls(subject)
  const [text, setText] = useState('')
  if (!access.canReview('comment')) return null
  const open = commentsOpen(subject.status)
  return (
    <Space direction="vertical" size={8} style={{ width: '100%', marginTop: 12 }}>
      <Input.TextArea
        aria-label="Comment"
        value={text}
        disabled={!open}
        onChange={(e) => setText(e.target.value)}
        placeholder={open ? 'Comment on this item' : `Comments are open while an item is proposed or in review (this one is ${statusLabel(subject.status).toLowerCase()})`}
        autoSize={{ minRows: 2, maxRows: 6 }}
      />
      <Button
        disabled={!open || !text.trim()}
        loading={calls.pending}
        onClick={() =>
          calls.post({ kind: 'comment', text: text.trim() }).then(
            () => setText(''),
            (error) => message.error(knowledgeErrorMessage(error), 6),
          )
        }
      >
        Add comment
      </Button>
    </Space>
  )
}

// ---- what happened -------------------------------------------------------------------------------------------

function Thread({ items }: { items: ReviewItem[] }) {
  if (items.length === 0) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No proposal, comment or ruling yet" />
  return (
    <Timeline
      items={items.map((item) => ({
        key: item.id,
        color: item.kind === 'ruling' ? (item.ruling === 'agreed' ? 'green' : 'red') : item.kind === 'proposal' ? 'blue' : 'gray',
        children: (
          <div>
            <Space size={6} wrap>
              <span style={{ fontWeight: 600 }}>{item.approver ?? item.author ?? 'unknown'}</span>
              <Muted>
                {item.kind === 'proposal' ? 'proposed it' : item.kind === 'ruling' ? 'ruled' : 'commented'} · {formatDate(item.date)}
              </Muted>
              {item.ruling && <Tag color={item.ruling === 'agreed' ? 'green' : 'red'}>{item.ruling}</Tag>}
              {item.linkStatus && <LinkStatusTag status={item.linkStatus} />}
            </Space>
            {item.text && <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{item.text}</div>}
          </div>
        ),
      }))}
    />
  )
}

function History({ items, reviews }: { items: TransitionRecord[]; reviews: ReviewItem[] }) {
  if (items.length === 0) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="It has not changed state yet" />
  return (
    <Timeline
      items={items.map((item) => {
        const review = reviews.find((r) => r.id === item.reviewItem)
        return {
          key: item.id,
          children: (
            <div>
              <Space size={6} wrap>
                <StatusTag status={item.from} />
                <span aria-label="to">→</span>
                <StatusTag status={item.to} />
                <Muted>
                  by {item.actor ?? 'unknown'} · {formatDate(item.date)}
                  {review && ` · ${review.kind}`}
                </Muted>
              </Space>
            </div>
          ),
        }
      })}
    />
  )
}

function Validation({ moduleId }: { moduleId: string }) {
  const access = useKnowledge()
  const query = useValidation(access.instance, moduleId)
  const run = useRunValidation(access.instance)
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {access.can('runKnowledgeSpecificationValidation') && (
        <Button loading={run.isPending} onClick={() => run.mutate(moduleId, { onError: (error) => message.error(knowledgeErrorMessage(error), 6) })}>
          Run validation
        </Button>
      )}
      {query.isLoading ? (
        <Spin />
      ) : query.isError ? (
        <QueryError error={query.error} what="the validation" onRetry={() => query.refetch()} />
      ) : query.data ? (
        <Collapse
          defaultActiveKey={['report']}
          items={[{ key: 'report', label: 'Last validation of the module', children: <ValidationReportView report={query.data} /> }]}
        />
      ) : (
        <Muted>The module has not been validated yet.</Muted>
      )}
    </Space>
  )
}
