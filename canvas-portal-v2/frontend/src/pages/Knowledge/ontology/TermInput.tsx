import React, { useEffect, useState } from 'react'
import { AutoComplete, Spin } from 'antd'
import { useTermSearch } from '@/api/knowledge'
import { colors } from '@/theme'
import { useKnowledge } from '../KnowledgeContext'
import { LayerTag } from '../shared'

interface TermInputProps {
  value?: string
  onChange?: (value: string) => void
  id?: string
  placeholder?: string
  'aria-label'?: string
}

/** An IRI or CURIE, with the ontology's terms suggested while typing. Works as a Form.Item control. */
export default function TermInput({ value, onChange, id, placeholder, 'aria-label': ariaLabel }: TermInputProps) {
  const { instance, can } = useKnowledge()
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setQ(typed.trim()), 300)
    return () => clearTimeout(timer)
  }, [typed])
  // Unpublished modules too: a bridge or a knowledge-base is often prepared before they are published.
  const search = useTermSearch(
    instance,
    { q, limit: 12, includeUnpublished: true },
    { enabled: q.length >= 2 && can('retrieveKnowledgeTerm') },
  )
  const hits = q.length >= 2 ? search.data?.hits ?? [] : []
  // One term can match several times (a label and a definition).
  const options = [...new Map(hits.map((hit) => [hit.curie ?? hit.iri ?? '', hit])).entries()]
    .filter(([term]) => term)
    .map(([term, hit]) => ({
      value: term,
      label: (
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div>{hit.text ?? term}</div>
            <div style={{ fontSize: 11, color: colors.textMuted }}>{term}</div>
          </div>
          {hit.layer && <LayerTag layer={hit.layer} />}
        </div>
      ),
    }))
  return (
    <AutoComplete
      id={id}
      aria-label={ariaLabel}
      value={value}
      onChange={(next) => onChange?.(next ?? '')}
      onSearch={setTyped}
      options={options}
      placeholder={placeholder}
      notFoundContent={search.isFetching ? <Spin size="small" /> : null}
      popupMatchSelectWidth={420}
      allowClear
    />
  )
}
