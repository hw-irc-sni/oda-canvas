import React, { createContext, useContext, useMemo } from 'react'
import type { KnowledgeInstance, LifecycleStatus } from '@/api/knowledge'

export type LifecycleKind = 'knowledge' | 'knowledgeSpecification'

interface KnowledgeContextValue {
  /** The selected instance's name, for the API calls. */
  instance: string
  info: KnowledgeInstance
  /** Whether the user's roles allow an operation of the instance's OAS. The component checks every call again. */
  can: (operationId: string) => boolean
  /** The states the user may move an item to from its current state. */
  nextStates: (kind: LifecycleKind, from: LifecycleStatus | undefined) => LifecycleStatus[]
  canTransition: (kind: LifecycleKind, from: LifecycleStatus | undefined, to: LifecycleStatus) => boolean
  /** Whether the user may post a kind of review item (proposal, comment or ruling). */
  canReview: (kind: string) => boolean
}

const KnowledgeContext = createContext<KnowledgeContextValue | null>(null)

export function KnowledgeProvider({ info, children }: { info: KnowledgeInstance; children: React.ReactNode }) {
  const value = useMemo<KnowledgeContextValue>(() => {
    const { capabilities } = info
    const nextStates: KnowledgeContextValue['nextStates'] = (kind, from) =>
      (capabilities?.transitions?.[kind] ?? []).filter((t) => t.from === from).map((t) => t.to)
    return {
      instance: info.name,
      info,
      can: (operationId) => capabilities?.operations?.[operationId] === true,
      nextStates,
      canTransition: (kind, from, to) => nextStates(kind, from).includes(to),
      canReview: (kind) => capabilities?.reviewKinds?.includes(kind) === true,
    }
  }, [info])
  return <KnowledgeContext.Provider value={value}>{children}</KnowledgeContext.Provider>
}

/** The selected instance and what the user may do on it. Only inside the page's sections. */
export function useKnowledge(): KnowledgeContextValue {
  const value = useContext(KnowledgeContext)
  if (!value) throw new Error('useKnowledge needs a KnowledgeProvider')
  return value
}
