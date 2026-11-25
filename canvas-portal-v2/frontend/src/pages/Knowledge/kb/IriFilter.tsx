import React, { useEffect, useState } from 'react'
import { Select, Spin } from 'antd'
import { useTermSearch } from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { isIri } from './sparql'

interface IriFilterProps {
  value: string[]
  onChange: (iris: string[]) => void
  placeholder: string
  'aria-label': string
}

/** Several IRIs: terms of the ontology, suggested while typing, or any IRI typed in full. */
export default function IriFilter({ value, onChange, placeholder, 'aria-label': ariaLabel }: IriFilterProps) {
  const { instance, can } = useKnowledge()
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setQ(typed.trim()), 300)
    return () => clearTimeout(timer)
  }, [typed])
  const search = useTermSearch(
    instance,
    { q, limit: 12, includeUnpublished: true },
    { enabled: q.length >= 2 && can('retrieveKnowledgeTerm') },
  )
  const hits = q.length >= 2 ? search.data?.hits ?? [] : []
  const options = [...new Map(hits.map((hit) => [hit.iri ?? '', hit])).entries()]
    .filter(([iri]) => iri)
    .map(([iri, hit]) => ({
      value: iri,
      label: (
        <div>
          <div>{hit.text ?? hit.curie ?? iri}</div>
          <div style={{ fontSize: 11, color: colors.textMuted }}>{iri}</div>
        </div>
      ),
    }))
  // An IRI typed in full can be used even if it's no term of the ontology (an entity of the knowledge-base).
  const own = typed.trim()
  if (/^(https?|urn):/.test(own) && isIri(own) && !options.some((o) => o.value === own)) {
    options.unshift({ value: own, label: <div>{own}</div> })
  }
  return (
    <Select
      mode="multiple"
      allowClear
      aria-label={ariaLabel}
      value={value}
      onChange={(next: string[]) => {
        onChange(next)
        setTyped('')
      }}
      searchValue={typed}
      onSearch={setTyped}
      filterOption={false}
      options={options}
      // The selected items show their IRI, whatever the options are now.
      optionLabelProp="value"
      notFoundContent={search.isFetching ? <Spin size="small" /> : null}
      placeholder={placeholder}
      style={{ width: '100%' }}
    />
  )
}
