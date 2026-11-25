import React, { useState } from 'react'
import { Button, Popconfirm, Radio, Segmented, Space, Table, Tag, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { knowledgeErrorMessage, useDecideProposal, useProposals, type Knowledge, type KnowledgeProposal } from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'
import { CodeBlock, Muted, NeutralTag, QueryError, formatDate } from '../shared'
import { isWritable } from './bases'

const KINDS: Record<string, string> = {
  dataConflict: 'Data conflict',
  ontologyTerm: 'Missing ontology term',
  entityLink: 'Ambiguous entity',
}

/** What the ingestion pipeline proposes instead of applying it. */
export default function ProposalsTab({ base }: { base: Knowledge }) {
  const { instance } = useKnowledge()
  const query = useProposals(instance, base.id)
  const [openOnly, setOpenOnly] = useState(true)
  const items = (query.data ?? []).filter((p) => !openOnly || p.state === 'proposed')

  const columns: ColumnsType<KnowledgeProposal> = [
    { title: 'Kind', key: 'kind', width: 170, render: (_, p) => <NeutralTag>{KINDS[p.kind ?? ''] ?? p.kind}</NeutralTag> },
    { title: 'Summary', dataIndex: 'summary', key: 'summary', render: (value?: string) => <span style={{ wordBreak: 'break-word' }}>{value ?? '—'}</span> },
    {
      title: 'State',
      key: 'state',
      width: 110,
      render: (_, p) =>
        p.state === 'accepted' ? <Tag color="green">accepted</Tag> : p.state === 'rejected' ? <Tag color="red">rejected</Tag> : <Tag color="blue">proposed</Tag>,
    },
    { title: 'Created', dataIndex: 'creationDate', key: 'creationDate', width: 170, render: (value?: string) => formatDate(value) },
  ]
  if (query.isError && !query.data) return <QueryError error={query.error} what="the proposals" onRetry={() => query.refetch()} />
  return (
    <div>
      <Segmented
        style={{ marginBottom: 12 }}
        value={openOnly ? 'open' : 'all'}
        onChange={(value) => setOpenOnly(value === 'open')}
        options={[{ label: 'To decide', value: 'open' }, { label: 'All', value: 'all' }]}
      />
      <Table<KnowledgeProposal>
        rowKey="id"
        size="small"
        loading={query.isLoading}
        columns={columns}
        dataSource={items}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        locale={{ emptyText: openOnly ? 'No proposal to decide.' : 'The pipeline has proposed nothing.' }}
        expandable={{ expandedRowRender: (proposal) => <Proposal base={base} proposal={proposal} /> }}
      />
    </div>
  )
}

function Proposal({ base, proposal }: { base: Knowledge; proposal: KnowledgeProposal }) {
  const access = useKnowledge()
  const decide = useDecideProposal(access.instance)
  const [choice, setChoice] = useState<string>()
  const listed = proposal.details?.candidates
  const candidates = Array.isArray(listed) ? listed.filter((c): c is string => typeof c === 'string') : []
  const needsChoice = proposal.kind === 'entityLink'
  const decidable =
    proposal.state === 'proposed' && proposal.kind !== 'ontologyTerm' && isWritable(base) && access.can('patchKnowledgeProposal')
  const rationale = proposal.suggestion?.rationale

  const submit = (state: 'accepted' | 'rejected') =>
    decide.mutate(
      { id: base.id, proposalId: proposal.id, decision: { state, ...(state === 'accepted' && needsChoice ? { choice } : {}) } },
      {
        onSuccess: () => message.success(state === 'accepted' ? 'Accepted' : 'Rejected'),
        onError: (error) => message.error(knowledgeErrorMessage(error), 6),
      },
    )

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      {typeof rationale === 'string' && (
        <div>
          <Muted>Suggestion:</Muted> {rationale}
        </div>
      )}
      <CodeBlock
        text={JSON.stringify({ ...proposal.details, ...(proposal.suggestion ? { suggestion: proposal.suggestion } : {}) }, null, 2)}
        maxHeight={240}
      />
      {proposal.state !== 'proposed' && (
        <Muted>
          {proposal.state} by {proposal.decidedBy ?? 'unknown'} · {formatDate(proposal.decidedAt)}
        </Muted>
      )}
      {proposal.state === 'proposed' && proposal.kind === 'ontologyTerm' && (
        <Muted>The ontology lacks this term: an ontology-editor can add it to a module.</Muted>
      )}
      {decidable && needsChoice && (
        <Radio.Group value={choice} onChange={(e) => setChoice(e.target.value as string)}>
          <Space direction="vertical">{candidates.map((c) => <Radio key={c} value={c}><span style={{ wordBreak: 'break-all' }}>{c}</span></Radio>)}</Space>
        </Radio.Group>
      )}
      {decidable && (
        <Space size={8}>
          <Popconfirm
            title={needsChoice ? 'Link the entity?' : 'Accept the data?'}
            description={needsChoice ? 'It is declared the same as the chosen entity.' : 'The withheld triples are written to the graph, conflict included.'}
            okText="Accept"
            onConfirm={() => submit('accepted')}
            disabled={needsChoice && !choice}
          >
            <Button type="primary" disabled={needsChoice && !choice} loading={decide.isPending}>Accept</Button>
          </Popconfirm>
          <Button danger loading={decide.isPending} onClick={() => submit('rejected')}>Reject</Button>
        </Space>
      )}
    </Space>
  )
}
