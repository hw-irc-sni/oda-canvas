import React, { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Avatar, Button, Empty, message, Popconfirm, Space, Spin, Table, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ApiOutlined, FileOutlined, PlusOutlined, ToolOutlined } from '@ant-design/icons'
import {
  fetchAiGateways,
  fetchMcpTargets,
  fetchModelConfigs,
  removeMcpTarget,
  removeModelConfig,
  type AiGatewayAgent,
  type AiGatewayModel,
  type AiGatewaysSummary,
  type AiGatewayTool,
  type AiGatewayTrafficPolicy,
  type McpResource,
  type McpTarget,
  type McpTool,
  type ModelConfig,
} from '@/api/aiGateways'
import SectionedPage, { PageSection } from '@/components/SectionedPage'
import { colors } from '@/theme'
import { useAuth } from '@/hooks/useAuth'
import { errorDetail } from './errorDetail'
import McpInspectorModal, { type InspectedItem } from './McpInspectorModal'
import McpTargetModal from './McpTargetModal'
import ModelConfigModal, { type EditedModel } from './ModelConfigModal'
import ModelTestModal from './ModelTestModal'

const renderMatches = (matches: string[]) => (
  <Space size={[4, 4]} wrap>
    {matches.map((match) => (
      <Tag key={match} style={{ fontFamily: 'monospace', marginInlineEnd: 0 }}>
        {match}
      </Tag>
    ))}
  </Space>
)

const ICON_SIZE = 32

/** The server's icon, or its first letter when it has none. */
function ServerAvatar({ name, icon }: { name: string; icon?: string | null }) {
  return (
    <Avatar
      size={ICON_SIZE}
      shape="square"
      // contain, not the default cover: a logo that isn't square is shown whole.
      src={icon ? <img src={icon} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain' }} /> : undefined}
      icon={name ? undefined : <ApiOutlined />}
      style={{ flex: 'none', fontSize: 16, backgroundColor: icon ? 'transparent' : colors.textMuted }}
    >
      {name.charAt(0).toUpperCase()}
    </Avatar>
  )
}

/** A tool's or resource's icon, or a plain symbol so the names stay aligned. */
function ItemAvatar({ icon, fallback }: { icon?: string | null; fallback: React.ReactNode }) {
  return (
    <Avatar
      size={ICON_SIZE}
      shape="square"
      src={icon ? <img src={icon} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain' }} /> : undefined}
      icon={icon ? undefined : fallback}
      style={{ flex: 'none', fontSize: 16, backgroundColor: 'transparent', color: colors.textMuted }}
    />
  )
}

function ServerName({ name, icon, label }: { name: string; icon?: string | null; label?: React.ReactNode }) {
  return (
    <Space size={8}>
      <ServerAvatar name={name} icon={icon} />
      {label ?? name}
    </Space>
  )
}

function RemoveButton({
  name,
  description,
  pending,
  onConfirm,
}: {
  name: string
  description: string
  pending: boolean
  onConfirm: () => void
}) {
  return (
    <Popconfirm
      title={`Remove ${name}?`}
      description={description}
      okText="Remove"
      okButtonProps={{ danger: true }}
      onConfirm={onConfirm}
    >
      <Button size="small" danger loading={pending} aria-label={`Remove ${name}`}>
        Remove
      </Button>
    </Popconfirm>
  )
}

function AddButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Tooltip title={label}>
      <Button size="small" icon={<PlusOutlined />} aria-label={label} onClick={onClick} />
    </Tooltip>
  )
}

interface ModelActions {
  onTest: (model: string) => void
  /** Only for admins, and only for the models that are in the gateway's llm.models list. */
  configFor: (model: string) => ModelConfig | undefined
  onEdit: (config: ModelConfig) => void
  onRemove: (model: string) => void
  removing: string | null
}

const modelColumns = ({ onTest, configFor, onEdit, onRemove, removing }: ModelActions): ColumnsType<AiGatewayModel> => [
  { title: 'Model', dataIndex: 'name' },
  { title: 'Provider', dataIndex: 'provider' },
  { title: 'Upstream', dataIndex: 'upstream' },
  { title: 'Paths', dataIndex: 'matches', render: renderMatches },
  {
    title: 'Status',
    dataIndex: 'status',
    render: (status: AiGatewayModel['status']) => (
      <Tag color={status === 'active' ? 'green' : 'red'}>{status}</Tag>
    ),
  },
  {
    title: 'Actions',
    key: 'actions',
    render: (_, model) => {
      const config = configFor(model.name)
      return (
        <Space size={8}>
          <Button
            size="small"
            disabled={model.status !== 'active'}
            aria-label={`Test ${model.name}`}
            onClick={() => onTest(model.name)}
          >
            Test
          </Button>
          {config && (
            <>
              <Button size="small" aria-label={`Edit ${model.name}`} onClick={() => onEdit(config)}>
                Edit
              </Button>
              <RemoveButton
                name={model.name}
                description="Clients can no longer call this model through the gateway."
                pending={removing === model.name}
                onConfirm={() => onRemove(model.name)}
              />
            </>
          )}
        </Space>
      )
    },
  },
]

interface TargetActions {
  /** Only for admins, and only for the servers that are in the gateway's mcp.targets list. */
  configFor: (name: string) => McpTarget | undefined
  onEdit: (target: McpTarget) => void
  onRemove: (name: string) => void
  removing: string | null
  isLastTarget: boolean
}

const toolColumns = ({ configFor, onEdit, onRemove, removing, isLastTarget }: TargetActions): ColumnsType<AiGatewayTool> => [
  { title: 'Target', dataIndex: 'name', render: (name: string, tool) => <ServerName name={name} icon={tool.icon} /> },
  { title: 'Upstream', dataIndex: 'upstream' },
  { title: 'Paths', dataIndex: 'matches', render: renderMatches },
  { title: 'Sessions', dataIndex: 'stateful', render: (stateful: boolean) => (stateful ? 'Stateful' : 'Stateless') },
  {
    title: 'Actions',
    key: 'actions',
    render: (_, tool) => {
      const target = configFor(tool.name)
      if (!target) return null
      return (
        <Space size={8}>
          <Button size="small" aria-label={`Edit ${tool.name}`} onClick={() => onEdit(target)}>
            Edit
          </Button>
          <RemoveButton
            name={tool.name}
            description={
              isLastTarget
                ? "This is the last MCP server, so the gateway's MCP endpoint stops working until another is added."
                : "Clients can no longer use this server's tools and resources through the gateway."
            }
            pending={removing === tool.name}
            onConfirm={() => onRemove(tool.name)}
          />
        </Space>
      )
    },
  },
]

const agentColumns: ColumnsType<AiGatewayAgent> = [
  { title: 'Agent', dataIndex: 'name' },
  { title: 'Paths', dataIndex: 'matches', render: renderMatches },
  { title: 'Backend', dataIndex: 'backend' },
]

const trafficColumns: ColumnsType<AiGatewayTrafficPolicy> = [
  { title: 'Route', dataIndex: 'route' },
  { title: 'Paths', dataIndex: 'matches', render: renderMatches },
  { title: 'Policy', dataIndex: 'policy' },
  {
    title: 'Settings',
    dataIndex: 'settings',
    render: (settings: AiGatewayTrafficPolicy['settings']) =>
      Object.entries(settings)
        .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : String(value)}`)
        .join(' · '),
  },
]

function ConfigTable<T extends object>({
  rows,
  columns,
  rowKey,
  emptyText,
}: {
  rows: T[]
  columns: ColumnsType<T>
  rowKey: (row: T, index?: number) => string
  emptyText: string
}) {
  if (rows.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} />
  }
  return <Table size="small" pagination={false} rowKey={rowKey} columns={columns} dataSource={rows} />
}

function InspectLink({ label, kind, onClick }: { label: string; kind: string; onClick: () => void }) {
  return (
    <Button
      type="link"
      size="small"
      style={{ padding: 0, height: 'auto', whiteSpace: 'normal', textAlign: 'left' }}
      aria-label={`Inspect ${kind} ${label}`}
      onClick={onClick}
    >
      {label}
    </Button>
  )
}

interface ServerGroup<T> {
  server: string
  items: T[]
}

/** agentgateway names a tool `<target>_<tool>` and a resource URI `<target>+<scheme>://...`. A target name can hold the
 * separator too, so the configured targets are matched first (longest first), then the text before the first separator. */
function groupByServer<T>(
  items: T[],
  keyOf: (item: T) => string,
  separator: string,
  targets: string[],
): ServerGroup<T>[] {
  const known = [...targets].sort((a, b) => b.length - a.length)
  const groups = new Map<string, T[]>()
  for (const item of items) {
    const key = keyOf(item)
    const index = key.indexOf(separator)
    const server = known.find((target) => key.startsWith(target + separator)) ?? (index > 0 ? key.slice(0, index) : '')
    groups.set(server, [...(groups.get(server) ?? []), item])
  }
  return [...groups].map(([server, grouped]) => ({ server, items: grouped }))
}

function GroupedTable<T extends object>({
  groups,
  icons,
  noun,
  columns,
  rowKey,
  emptyText,
}: {
  groups: ServerGroup<T>[]
  icons: Map<string, string | null | undefined>
  noun: string
  columns: ColumnsType<T>
  rowKey: (row: T) => string
  emptyText: string
}) {
  if (groups.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} />
  }
  return (
    <Table<ServerGroup<T>>
      size="small"
      pagination={false}
      rowKey={(group) => group.server}
      dataSource={groups}
      columns={[
        {
          title: 'MCP server',
          dataIndex: 'server',
          render: (server: string) => (
            <ServerName
              name={server}
              icon={icons.get(server)}
              label={server || <span style={{ color: colors.textMuted }}>No server prefix</span>}
            />
          ),
        },
        {
          title: noun,
          key: 'count',
          render: (_, group) => <Tag style={{ marginInlineEnd: 0 }}>{group.items.length}</Tag>,
        },
      ]}
      expandable={{
        expandRowByClick: true,
        expandedRowRender: (group) => (
          <Table size="small" pagination={false} rowKey={rowKey} columns={columns} dataSource={group.items} />
        ),
      }}
    />
  )
}

function LiveDiscovery({ data, onInspect }: { data: AiGatewaysSummary; onInspect: (item: InspectedItem) => void }) {
  const headingStyle = { fontSize: 12, fontWeight: 600, color: colors.textSecondary, margin: '16px 0 8px' }
  if (!data.discovered) {
    return (
      <Alert
        style={{ marginTop: 16 }}
        type="warning"
        showIcon
        message="Live tools and resources are unavailable"
        description={data.discoveryError ?? undefined}
      />
    )
  }
  const { tools, resources, partial } = data.discovered
  const targets = (data.tools ?? []).map((target) => target.name)
  const icons = new Map((data.tools ?? []).map((target) => [target.name, target.icon]))
  return (
    <>
      <h4 style={headingStyle}>Tools available now</h4>
      <GroupedTable
        groups={groupByServer(tools, (tool) => tool.name, '_', targets)}
        icons={icons}
        noun="Tools"
        rowKey={(tool) => tool.name}
        emptyText="No tools available"
        columns={[
          {
            title: 'Tool',
            dataIndex: 'name',
            render: (name: string, tool: McpTool) => (
              <Space size={8}>
                <ItemAvatar icon={tool.icon} fallback={<ToolOutlined />} />
                <InspectLink label={name} kind="tool" onClick={() => onInspect({ kind: 'tool', tool })} />
              </Space>
            ),
          },
          { title: 'Description', dataIndex: 'description' },
        ]}
      />
      <h4 style={headingStyle}>Resources available now</h4>
      <GroupedTable
        groups={groupByServer(resources, (resource) => resource.uri, '+', targets)}
        icons={icons}
        noun="Resources"
        rowKey={(resource) => resource.uri}
        emptyText="No resources available"
        columns={[
          {
            title: 'Resource',
            dataIndex: 'name',
            render: (name: string, resource: McpResource) => (
              <Space size={8}>
                <ItemAvatar icon={resource.icon} fallback={<FileOutlined />} />
                <InspectLink
                  label={name || resource.uri}
                  kind="resource"
                  onClick={() => onInspect({ kind: 'resource', resource })}
                />
              </Space>
            ),
          },
          { title: 'URI', dataIndex: 'uri' },
          { title: 'Description', dataIndex: 'description' },
        ]}
      />
      {partial && (
        <p style={{ fontSize: 12, color: colors.textMuted, margin: '8px 0 0' }}>
          Showing the first page only.
        </p>
      )}
    </>
  )
}

function buildSections(
  data: AiGatewaysSummary | undefined,
  modelActions: ModelActions,
  onAddModel: (() => void) | null,
  targetActions: TargetActions,
  onAddTarget: (() => void) | null,
  onInspect: (item: InspectedItem) => void,
): PageSection[] {
  const ok = data?.state === 'ok'
  return [
    {
      key: 'models',
      title: 'Models',
      description: 'LLM consumption: the models that clients can call through the gateway.',
      link: { to: '/model-gateway-services', label: 'Open Model Gateway services' },
      actions: ok && onAddModel ? <AddButton label="Add a model" onClick={onAddModel} /> : undefined,
      content: ok ? (
        <ConfigTable
          rows={data.models ?? []}
          columns={modelColumns(modelActions)}
          rowKey={(model) => `${model.name}/${model.upstream}`}
          emptyText="No models are configured"
        />
      ) : undefined,
    },
    {
      key: 'tools-resources',
      title: 'Tools & Resources',
      description: 'MCP connectivity: the configured MCP targets, and what they serve through the gateway right now.',
      actions: ok && onAddTarget ? <AddButton label="Add an MCP server" onClick={onAddTarget} /> : undefined,
      content: ok ? (
        <>
          <ConfigTable
            rows={data.tools ?? []}
            columns={toolColumns(targetActions)}
            rowKey={(tool) => tool.name}
            emptyText="No MCP targets are configured"
          />
          <LiveDiscovery data={data} onInspect={onInspect} />
        </>
      ) : undefined,
    },
    {
      key: 'agents',
      title: 'Agents',
      description: 'Agent connectivity: the routes that carry agent-to-agent (A2A) traffic.',
      content: ok ? (
        <ConfigTable
          rows={data.agents ?? []}
          columns={agentColumns}
          rowKey={(agent) => `${agent.name}/${agent.backend}`}
          emptyText="No agent routes are configured"
        />
      ) : undefined,
    },
    {
      key: 'traffic',
      title: 'Traffic Management',
      description: 'The timeout, retry, rate limit and other policies applied to each route.',
      content: ok ? (
        <ConfigTable
          rows={data.traffic ?? []}
          columns={trafficColumns}
          rowKey={(row, index) => `${row.route}/${row.policy}/${index}`}
          emptyText="No traffic policies are configured"
        />
      ) : undefined,
    },
  ]
}

const STATE_TITLES: Record<Exclude<AiGatewaysSummary['state'], 'ok'>, string> = {
  not_deployed: 'agentgateway is not deployed',
  unauthorized: 'The portal is not authorized to read the agentgateway configuration',
  unreachable: 'agentgateway is unreachable',
}

function Banner({ data, isLoading, isError }: { data?: AiGatewaysSummary; isLoading: boolean; isError: boolean }) {
  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}>
        <Spin />
      </div>
    )
  }
  if (isError || !data) {
    return <Alert type="error" showIcon message="Couldn't load the AI gateway configuration" />
  }
  if (data.state !== 'ok') {
    return <Alert type="warning" showIcon message={STATE_TITLES[data.state]} description={data.message} />
  }
  const warnings = data.warnings ?? []
  const reloadFailed = data.runtime?.synchronized === false
  if (!reloadFailed && warnings.length === 0) return null
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      {reloadFailed && (
        <Alert
          type="warning"
          showIcon
          message="The latest configuration was not applied, so this page shows the configuration still running"
          description="Check the agentgateway logs for the error."
        />
      )}
      {warnings.map((warning) => (
        <Alert key={warning} type="warning" showIcon message={warning} />
      ))}
    </Space>
  )
}

export default function GatewaysPage() {
  const [testedModel, setTestedModel] = useState<string | null>(null)
  const [inspected, setInspected] = useState<InspectedItem | null>(null)
  const [editedModel, setEditedModel] = useState<EditedModel | null>(null)
  const [editedTarget, setEditedTarget] = useState<{ target: McpTarget | null } | null>(null)
  const { isAdmin } = useAuth()
  const queryClient = useQueryClient()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['ai-gateways'],
    queryFn: fetchAiGateways,
    refetchInterval: 30_000,
    retry: 1,
  })
  const { data: modelConfigs } = useQuery({
    queryKey: ['ai-gateways', 'model-configs'],
    queryFn: fetchModelConfigs,
    enabled: isAdmin,
    refetchInterval: 30_000,
    retry: 1,
  })
  const { data: mcpTargets } = useQuery({
    queryKey: ['ai-gateways', 'mcp-targets'],
    queryFn: fetchMcpTargets,
    enabled: isAdmin,
    refetchInterval: 30_000,
    retry: 1,
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['ai-gateways'] })
  const removeModel = useMutation({
    mutationFn: removeModelConfig,
    onSuccess: (result) => {
      message.success(result.applied ? 'Model removed' : result.message)
      void refresh()
    },
    onError: (error) => {
      message.error(errorDetail(error))
      void refresh()
    },
  })

  const removeTarget = useMutation({
    mutationFn: removeMcpTarget,
    onSuccess: (result) => {
      message.success(result.applied ? 'MCP server removed' : result.message)
      void refresh()
    },
    onError: (error) => {
      message.error(errorDetail(error))
      void refresh()
    },
  })

  const targetActions: TargetActions = {
    configFor: (name) => mcpTargets?.targets.find((target) => target.name === name),
    onEdit: (target) => setEditedTarget({ target }),
    onRemove: (name) => removeTarget.mutate(name),
    removing: removeTarget.isPending ? (removeTarget.variables ?? null) : null,
    isLastTarget: mcpTargets?.targets.length === 1,
  }

  const modelActions: ModelActions = {
    onTest: setTestedModel,
    configFor: (name) => modelConfigs?.models.find((config) => config.name === name),
    onEdit: (config) => setEditedModel({ config }),
    onRemove: (name) => removeModel.mutate(name),
    removing: removeModel.isPending ? (removeModel.variables ?? null) : null,
  }

  return (
    <>
      <SectionedPage
        title="AI Gateways"
        description={data?.version ? `Active configuration of agentgateway ${data.version}` : 'Active configuration of agentgateway'}
        icon={<ApiOutlined />}
        hideWipBadge
        banner={<Banner data={data} isLoading={isLoading} isError={isError} />}
        sections={buildSections(
          data,
          modelActions,
          modelConfigs?.configurable ? () => setEditedModel({ config: null }) : null,
          targetActions,
          mcpTargets?.configurable ? () => setEditedTarget({ target: null }) : null,
          setInspected,
        )}
      />
      {testedModel && <ModelTestModal model={testedModel} onClose={() => setTestedModel(null)} />}
      {editedModel && (
        <ModelConfigModal
          config={editedModel.config}
          onSaved={() => {
            void refresh()
            setEditedModel(null)
          }}
          onClose={() => setEditedModel(null)}
        />
      )}
      {editedTarget && (
        <McpTargetModal
          target={editedTarget.target}
          onSaved={() => {
            void refresh()
            setEditedTarget(null)
          }}
          onClose={() => setEditedTarget(null)}
        />
      )}
      {inspected && <McpInspectorModal item={inspected} onClose={() => setInspected(null)} />}
    </>
  )
}
