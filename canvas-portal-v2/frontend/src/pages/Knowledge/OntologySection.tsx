import React, { useState } from 'react'
import { useKnowledge } from './KnowledgeContext'
import MappingEditor from './ontology/MappingEditor'
import ModuleRegistry from './ontology/ModuleRegistry'
import ReviewQueue from './ontology/ReviewQueue'
import TermExplorer from './ontology/TermExplorer'
import SectionTabs, { type SectionTab } from './SectionTabs'

export default function OntologySection() {
  const { can } = useKnowledge()
  const [active, setActive] = useState<string>()
  // The module to open in the review queue, from its details.
  const [reviewing, setReviewing] = useState<string>()

  const tabs: SectionTab[] = [
    {
      key: 'modules',
      label: 'Modules',
      operation: 'listKnowledgeSpecification',
      content: (
        <ModuleRegistry
          onReview={
            can('listKnowledgeSpecificationReview')
              ? (id) => {
                  setReviewing(id)
                  setActive('reviews')
                }
              : undefined
          }
        />
      ),
    },
    { key: 'terms', label: 'Terms', operation: 'retrieveKnowledgeTerm', content: <TermExplorer /> },
    { key: 'mappings', label: 'Mappings', operation: 'listKnowledgeSpecificationMapping', content: <MappingEditor /> },
    {
      key: 'reviews',
      label: 'Reviews',
      operation: 'listKnowledgeSpecificationReview',
      content: <ReviewQueue openModule={reviewing} onOpened={() => setReviewing(undefined)} />,
    },
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
