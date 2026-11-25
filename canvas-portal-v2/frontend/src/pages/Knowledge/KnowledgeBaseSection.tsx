import React, { useState } from 'react'
import SectionTabs, { type SectionTab } from './SectionTabs'

const tabs: SectionTab[] = []

export default function KnowledgeBaseSection() {
  const [active, setActive] = useState<string>()
  return <SectionTabs active={active} onChange={setActive} tabs={tabs} denied="Your roles on this instance don't include reading its knowledge-bases." />
}
