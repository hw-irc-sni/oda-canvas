import React from 'react'
import { Button, Select, Space, Tag, Tooltip } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { KnowledgeInstance } from '@/api/knowledge'
import { colors } from '@/theme'

const STORAGE_KEY = 'canvas-portal.knowledge.instance'

/** The instance chosen last in this browser, if storage works. */
export function savedInstance(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export function saveInstance(name: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, name)
  } catch {
    // Only a convenience: the choice then lasts until the page is left.
  }
}

/** The wanted instance if it can be used, else the first one that can, else the first one. */
export function pickInstance(instances: KnowledgeInstance[], wanted: string | null): KnowledgeInstance | undefined {
  return (
    instances.find((instance) => instance.name === wanted && instance.available) ??
    instances.find((instance) => instance.available) ??
    instances[0]
  )
}

interface InstancePickerProps {
  instances: KnowledgeInstance[]
  selected: KnowledgeInstance
  onSelect: (name: string) => void
  onRefresh: () => void
  refreshing: boolean
}

export default function InstancePicker({ instances, selected, onSelect, onRefresh, refreshing }: InstancePickerProps) {
  return (
    <Space size={8}>
      <span style={{ fontSize: 12, color: colors.textMuted }}>Instance</span>
      <Select
        aria-label="Knowledge instance"
        value={selected.name}
        onChange={onSelect}
        style={{ minWidth: 260 }}
        popupMatchSelectWidth={false}
        optionLabelProp="label"
        options={instances.map((instance) => ({
          value: instance.name,
          label: instance.label,
          disabled: !instance.available,
          instance,
        }))}
        optionRender={(option) => {
          const instance = (option.data as { instance: KnowledgeInstance }).instance
          return (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
              <div>
                <div>{instance.label}</div>
                <div style={{ fontSize: 11, color: colors.textMuted }}>
                  {[instance.namespace, instance.version].filter(Boolean).join(' · ') || instance.name}
                </div>
              </div>
              {instance.available ? (
                <Tag color="green" style={{ marginInlineEnd: 0 }}>available</Tag>
              ) : (
                <Tag style={{ marginInlineEnd: 0 }}>{instance.deploymentStatus || instance.resourceStatus || 'unavailable'}</Tag>
              )}
            </div>
          )
        }}
      />
      <Tooltip title="Refresh the instances">
        <Button aria-label="Refresh the instances" icon={<ReloadOutlined />} loading={refreshing} onClick={onRefresh} />
      </Tooltip>
    </Space>
  )
}
