import React from 'react'
import SectionTabs, { type SectionTab } from './SectionTabs'

const tabs: SectionTab[] = []

export default function OntologySection() {
  return <SectionTabs tabs={tabs} denied="Your roles on this instance don't include reading its ontology." />
}
