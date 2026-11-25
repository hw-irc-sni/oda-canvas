import React, { useState } from 'react'
import { Alert, Tabs } from 'antd'
import { colors } from '@/theme'
import { useKnowledge } from './KnowledgeContext'

export interface SectionTab {
  key: string
  label: string
  /** The operation that reads what the tab shows: without it the tab is hidden. */
  operation: string
  content: React.ReactNode
}

/** The tabs of a section that the user may read. */
export default function SectionTabs({ tabs, denied }: { tabs: SectionTab[]; denied: string }) {
  const { can } = useKnowledge()
  const [active, setActive] = useState<string>()
  const visible = tabs.filter((tab) => can(tab.operation))

  if (tabs.length > 0 && visible.length === 0) return <Alert type="info" showIcon message={denied} />
  if (visible.length === 0) {
    return (
      <div
        style={{
          border: `1px dashed ${colors.border}`,
          borderRadius: 10,
          padding: 14,
          background: colors.surfaceSubtle,
          fontSize: 12,
          color: colors.textMuted,
        }}
      >
        Nothing to show yet.
      </div>
    )
  }
  return (
    <Tabs
      activeKey={visible.some((tab) => tab.key === active) ? active : visible[0].key}
      onChange={setActive}
      items={visible.map((tab) => ({ key: tab.key, label: tab.label, children: tab.content }))}
    />
  )
}
