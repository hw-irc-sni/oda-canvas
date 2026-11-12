import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Input, Select } from 'antd'
import { callMcpTool, type McpCallOutcome, type McpOptionsSource, type McpTool } from '@/api/aiGateways'
import { colors } from '@/theme'
import { serverOf } from './mcpNames'

export interface McpOption {
  value: string
  label: string
}

/** Lists a field's choices with the gateway name of its `x-options` tool, or null when there's none to call. */
export type OptionsLookup = (source: McpOptionsSource) => string | null

const MAX_OPTIONS = 1000

/**
 * The gateway name of the tool an `x-options` source names, which must be on the same server as the tool
 * being tested: a source can't reach another server's tools, even one whose name extends this server's.
 * Only tools the gateway lists and that say they are read-only are called, since opening a form must never
 * change anything.
 */
export function optionsLookup(server: string, tools: McpTool[], targets: string[]): OptionsLookup {
  return (source) => {
    const name = server ? `${server}_${source.tool}` : source.tool
    if (serverOf(name, '_', targets) !== server) return null
    const tool = tools.find((candidate) => candidate.name === name)
    return tool?.annotations.readOnlyHint === true ? name : null
  }
}

/** The text of a tool result, parsed as JSON. */
function resultJson(outcome: McpCallOutcome): unknown {
  const response = outcome.exchanges[outcome.exchanges.length - 1]?.response
  if (outcome.error || !response || response.error) throw new Error(outcome.error ?? response?.error?.message ?? 'No response')
  const result = response.result as { content?: { type?: string; text?: unknown }[]; isError?: boolean } | undefined
  const text = result?.content?.find((item) => item.type === 'text')?.text
  if (result?.isError || typeof text !== 'string') throw new Error('The tool returned no list')
  return JSON.parse(text)
}

export function optionsFrom(source: McpOptionsSource, data: unknown): McpOption[] {
  const rows = source.items ? (data as Record<string, unknown> | null)?.[source.items] : data
  if (!Array.isArray(rows)) throw new Error('The tool returned no list')
  const seen = new Set<string>()
  const options: McpOption[] = []
  for (const row of rows.slice(0, MAX_OPTIONS)) {
    if (typeof row !== 'object' || row === null) continue
    const value = (row as Record<string, unknown>)[source.value]
    const label = source.label ? (row as Record<string, unknown>)[source.label] : undefined
    if (typeof value !== 'string' || !value || seen.has(value)) continue
    seen.add(value)
    options.push({ value, label: typeof label === 'string' ? label : '' })
  }
  return options
}

function OptionLabel({ option }: { option: McpOption }) {
  if (!option.label || option.label === option.value) return <span>{option.value}</span>
  return (
    <span>
      {option.label} <span style={{ color: colors.textMuted, fontSize: 12 }}>{option.value}</span>
    </span>
  )
}

/**
 * A select of the values the server lists for a field. While they load, or if they can't be listed or there
 * are none, it's a text box, so the form can always be filled in.
 */
export default function McpOptionsSelect({
  tool,
  source,
  value,
  onChange,
  disabled,
  label,
}: {
  tool: string
  source: McpOptionsSource
  value: unknown
  onChange: (value: unknown) => void
  disabled: boolean
  label: string
}) {
  const query = useQuery({
    queryKey: ['ai-gateways', 'mcp-options', tool, source],
    queryFn: async ({ signal }) => optionsFrom(source, resultJson(await callMcpTool(tool, source.arguments, signal))),
    staleTime: 30_000,
    retry: false,
  })
  const text = typeof value === 'string' ? value : ''

  if (query.data?.length) {
    return (
      <Select
        showSearch
        allowClear
        aria-label={label}
        disabled={disabled}
        placeholder="Choose a value"
        value={text || undefined}
        onChange={(next?: string) => onChange(next)}
        filterOption={(input, option) =>
          `${option?.label ?? ''} ${option?.value ?? ''}`.toLowerCase().includes(input.trim().toLowerCase())
        }
        optionRender={(option) => <OptionLabel option={option.data as McpOption} />}
        labelRender={({ value: selected }) => {
          const option = query.data.find((candidate) => candidate.value === selected)
          return option ? <OptionLabel option={option} /> : <span>{String(selected)}</span>
        }}
        options={query.data}
        notFoundContent={<span style={{ fontSize: 12 }}>No matching value</span>}
      />
    )
  }
  const note = query.isPending
    ? 'Loading the values…'
    : query.isError
      ? `Couldn't list the values (${query.error.message}).`
      : 'The server lists no values for this field yet.'
  return (
    <div>
      <Input aria-label={label} disabled={disabled} value={text} onChange={(event) => onChange(event.target.value)} />
      <div style={{ fontSize: 12, color: colors.textMuted, marginTop: 2 }} aria-live="polite">
        {note}
      </div>
    </div>
  )
}
