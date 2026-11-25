import React, { useState } from 'react'
import KnowledgeBaseList from './kb/KnowledgeBaseList'
import SectionTabs, { type SectionTab } from './SectionTabs'

const tabs: SectionTab[] = [
  { key: 'bases', label: 'Knowledge-bases', operation: 'listKnowledge', content: <KnowledgeBaseList /> },
]

export default function KnowledgeBaseSection() {
  const [active, setActive] = useState<string>()
  return (
    <SectionTabs
      tabs={tabs}
      active={active}
      onChange={setActive}
      denied="Your roles on this instance don't include reading its knowledge-bases."
    />
  )
}
