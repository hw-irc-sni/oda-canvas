import React from 'react'
import ModuleRegistry from './ontology/ModuleRegistry'
import SectionTabs, { type SectionTab } from './SectionTabs'

const tabs: SectionTab[] = [
  { key: 'modules', label: 'Modules', operation: 'listKnowledgeSpecification', content: <ModuleRegistry /> },
]

export default function OntologySection() {
  return <SectionTabs tabs={tabs} denied="Your roles on this instance don't include reading its ontology." />
}
