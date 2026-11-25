import React from 'react'
import ModuleRegistry from './ontology/ModuleRegistry'
import TermExplorer from './ontology/TermExplorer'
import SectionTabs, { type SectionTab } from './SectionTabs'

const tabs: SectionTab[] = [
  { key: 'modules', label: 'Modules', operation: 'listKnowledgeSpecification', content: <ModuleRegistry /> },
  { key: 'terms', label: 'Terms', operation: 'retrieveKnowledgeTerm', content: <TermExplorer /> },
]

export default function OntologySection() {
  return <SectionTabs tabs={tabs} denied="Your roles on this instance don't include reading its ontology." />
}
