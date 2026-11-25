import React, { useState } from 'react'
import { useKnowledge } from './KnowledgeContext'
import FederationPeers from './ontology/FederationPeers'
import MappingEditor from './ontology/MappingEditor'
import ModuleRegistry from './ontology/ModuleRegistry'
import ReviewQueue from './ontology/ReviewQueue'
import TermExplorer from './ontology/TermExplorer'
import ValidationPanel from './ontology/ValidationPanel'
import SectionTabs, { type SectionTab } from './SectionTabs'

export default function OntologySection() {
  const { can } = useKnowledge()
  const [active, setActive] = useState<string>()
  // The module to open in the review queue, from its details.
  const [reviewing, setReviewing] = useState<string>()

  const review = can('listKnowledgeSpecificationReview')
    ? (id: string) => {
        setReviewing(id)
        setActive('reviews')
      }
    : undefined
  const tabs: SectionTab[] = [
    {
      key: 'modules',
      label: 'Modules',
      operation: 'listKnowledgeSpecification',
      content: <ModuleRegistry onReview={review} />,
    },
    { key: 'terms', label: 'Terms', operation: 'retrieveKnowledgeTerm', content: <TermExplorer /> },
    { key: 'mappings', label: 'Mappings', operation: 'listKnowledgeSpecificationMapping', content: <MappingEditor /> },
    {
      key: 'reviews',
      label: 'Reviews',
      operation: 'listKnowledgeSpecificationReview',
      content: <ReviewQueue openModule={reviewing} onOpened={() => setReviewing(undefined)} />,
    },
    {
      key: 'validation',
      label: 'Validation',
      operation: 'retrieveKnowledgeSpecificationValidation',
      content: <ValidationPanel />,
    },
    { key: 'peers', label: 'Peers', operation: 'listKnowledgeSpecification', content: <FederationPeers onReview={review} /> },
  ]
  return (
    <SectionTabs
      tabs={tabs}
      active={active}
      onChange={setActive}
      denied="Your roles on this instance don't include reading its ontology."
    />
  )
}
