import type { KnowledgeSpecification, LifecycleStatus, Mapping, ReviewTarget } from '@/api/knowledge'
import type { useKnowledge } from '../KnowledgeContext'
import { moduleName } from './modules'

type Access = ReturnType<typeof useKnowledge>

/** A module, or a mapping of a bridge module, with its place in the governed lifecycle. */
export interface ReviewSubject {
  key: string
  target: ReviewTarget
  /** The module, or the bridge module of the mapping. */
  module: KnowledgeSpecification
  mapping?: Mapping
  status?: LifecycleStatus
  title: string
}

export function moduleSubject(module: KnowledgeSpecification): ReviewSubject {
  return {
    key: `module:${module.id}`,
    target: { kind: 'module', id: module.id },
    module,
    status: module.lifecycleStatus,
    title: moduleName(module),
  }
}

export function mappingSubject(module: KnowledgeSpecification, mapping: Mapping): ReviewSubject {
  return {
    key: `mapping:${module.id}/${mapping.id}`,
    target: { kind: 'mapping', id: mapping.id },
    module,
    mapping,
    status: mapping.lifecycleStatus,
    title: [mapping.subject, mapping.predicate, mapping.object].filter(Boolean).join(' '),
  }
}

export type ReviewAction = 'propose' | 'start' | 'comment' | 'rule' | 'publish' | 'deprecate' | 'revise'

export const ACTION_LABELS: Record<ReviewAction, string> = {
  propose: 'Propose',
  start: 'Start the review',
  comment: 'Comment',
  rule: 'Rule',
  publish: 'Publish',
  deprecate: 'Deprecate',
  revise: 'Revise',
}

/** The lifecycle changes made by a PATCH, with the state they lead to. */
export const PATCH_ACTIONS: Partial<Record<ReviewAction, LifecycleStatus>> = {
  start: 'inReview',
  publish: 'published',
  deprecate: 'deprecated',
  revise: 'draft',
}

/** Comments are open while an item is proposed or in review (the component's reviewComments). */
export function commentsOpen(status: LifecycleStatus | undefined): boolean {
  return status === 'proposed' || status === 'inReview'
}

/** What the user may do with an item now: allowed by the capabilities and possible in its state. */
export function reviewActions(access: Access, status: LifecycleStatus | undefined): ReviewAction[] {
  const may = (to: LifecycleStatus) => access.canTransition('knowledgeSpecification', status, to)
  const actions: ReviewAction[] = []
  if (status === 'draft' && access.canReview('proposal') && may('proposed')) actions.push('propose')
  if (status === 'proposed' && may('inReview')) actions.push('start')
  if (commentsOpen(status) && access.canReview('comment')) actions.push('comment')
  if (status === 'inReview' && access.canReview('ruling') && (may('agreed') || may('rejected'))) actions.push('rule')
  if (status === 'agreed' && may('published')) actions.push('publish')
  if (status === 'published' && may('deprecated')) actions.push('deprecate')
  if (status === 'rejected' && may('draft')) actions.push('revise')
  return actions
}
