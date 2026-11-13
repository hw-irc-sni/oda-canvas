import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Input, Modal, Spin, Table, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { CheckOutlined } from '@ant-design/icons'
import { fetchUpstreamModels, type ModelProvider, type UpstreamModel, type UpstreamModels } from '@/api/aiGateways'
import { colors } from '@/theme'
import { errorDetail } from './errorDetail'

/** What the model form holds when Browse is pressed. */
export interface UpstreamQuery {
  provider: ModelProvider
  baseUrl?: string
  apiKey?: string
}

type State = { status: 'loading' } | { status: 'failed'; message: string } | { status: 'loaded'; result: UpstreamModels }

// OpenAI-style lists give created as Unix seconds.
const TIME_COLUMNS = new Set(['created', 'created_at'])

function cellText(key: string, value: UpstreamModel[string]): string {
  if (value === null || value === undefined) return ''
  if (TIME_COLUMNS.has(key) && typeof value === 'number') return new Date(value * 1000).toLocaleDateString()
  return String(value)
}

function compare(a: UpstreamModel[string], b: UpstreamModel[string]): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a ?? '').localeCompare(String(b ?? ''))
}

/** The models the provider offers, to pick the upstream model from. */
export default function UpstreamModelsModal({
  query,
  current,
  onSelect,
  onClose,
}: {
  query: UpstreamQuery
  current?: string
  onSelect: (id: string) => void
  onClose: () => void
}) {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [search, setSearch] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    fetchUpstreamModels(query.provider, query.baseUrl, query.apiKey, controller.signal)
      .then((result) => setState({ status: 'loaded', result }))
      .catch((error) => {
        if (!controller.signal.aborted) setState({ status: 'failed', message: errorDetail(error) })
      })
    return () => controller.abort()
  }, [query])

  const models = state.status === 'loaded' ? state.result.models : []

  const columns = useMemo<ColumnsType<UpstreamModel>>(() => {
    const keys: string[] = []
    for (const model of models) for (const key of Object.keys(model)) if (!keys.includes(key)) keys.push(key)
    // object is "model" on every row of an OpenAI list, so it tells the user nothing.
    const shown = ['id', ...keys.filter((key) => key !== 'id' && key !== 'object')]
    return [
      {
        key: 'select',
        title: '',
        width: 92,
        fixed: 'left',
        render: (_, model) =>
          model.id === current ? (
            <Tag icon={<CheckOutlined />} color="success">
              In use
            </Tag>
          ) : (
            <Button size="small" type="primary" onClick={() => onSelect(model.id)}>
              Select
            </Button>
          ),
      },
      ...shown.map((key) => ({
        key,
        dataIndex: key,
        title: key,
        sorter: (a: UpstreamModel, b: UpstreamModel) => compare(a[key], b[key]),
        defaultSortOrder: key === 'id' ? ('ascend' as const) : undefined,
        ellipsis: { showTitle: false },
        render: (value: UpstreamModel[string]) => {
          const text = cellText(key, value)
          const shownText = key === 'id' ? <code>{text}</code> : text
          return text.length > 40 ? <Tooltip title={text}>{shownText}</Tooltip> : shownText
        },
      })),
    ]
  }, [models, current, onSelect])

  const needle = search.trim().toLowerCase()
  const rows = needle
    ? models.filter((model) => Object.values(model).some((value) => String(value ?? '').toLowerCase().includes(needle)))
    : models

  return (
    <Modal
      open
      title="Available models"
      width={960}
      onCancel={onClose}
      footer={<Button onClick={onClose}>Cancel</Button>}
    >
      {state.status === 'loading' && (
        <div style={{ textAlign: 'center', padding: 32 }}>
          <Spin tip="Asking the provider for its models">
            <div style={{ height: 48 }} />
          </Spin>
        </div>
      )}
      {state.status === 'failed' && <Alert type="error" showIcon message={state.message} />}
      {state.status === 'loaded' && (
        <>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
            <Input.Search
              allowClear
              autoFocus
              placeholder="Filter models"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              style={{ maxWidth: 320 }}
            />
            <span style={{ fontSize: 12, color: colors.textMuted }}>
              {rows.length === models.length ? `${models.length} models` : `${rows.length} of ${models.length} models`}{' '}
              from <code>{state.result.baseUrl}</code>
            </span>
          </div>
          {state.result.truncated && (
            <Alert style={{ marginBottom: 12 }} type="info" showIcon message="The provider lists more models than are shown." />
          )}
          <Table<UpstreamModel>
            size="small"
            rowKey="id"
            columns={columns}
            dataSource={rows}
            scroll={{ x: 'max-content', y: 420 }}
            pagination={{ pageSize: 50, showSizeChanger: false, hideOnSinglePage: true }}
            locale={{ emptyText: models.length ? 'No model matches the filter.' : 'The provider lists no models.' }}
          />
        </>
      )}
    </Modal>
  )
}
