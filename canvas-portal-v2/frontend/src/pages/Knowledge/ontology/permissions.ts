import type { KnowledgeSpecification, LifecycleStatus } from '@/api/knowledge'
import type { useKnowledge } from '../KnowledgeContext'
import { isFederated } from './modules'

type Access = ReturnType<typeof useKnowledge>

/** The states in which the component lets an editor change a module or a mapping. */
export function isEditable(status: LifecycleStatus | undefined): boolean {
  return status === 'draft' || status === 'rejected'
}

/** An item an editor may delete, or any item for who may rule on it (the component's rule for deletions). */
export function mayDelete(access: Access, operationId: string, status: LifecycleStatus | undefined): boolean {
  return access.can(operationId) && (isEditable(status) || access.canReview('ruling'))
}

/**
 * What the user may do with a module now. Changing fields shares an operation with the lifecycle transitions, which
 * reviewers and approvers may call too: who may register modules may edit them.
 */
export function modulePermissions(access: Access, module: KnowledgeSpecification) {
  const federated = isFederated(module)
  const author = !federated && access.can('createKnowledgeSpecification') && isEditable(module.lifecycleStatus)
  return {
    edit: author && access.can('patchKnowledgeSpecification'),
    replaceContent: author && access.can('replaceKnowledgeSpecificationContent') && module.source?.kind === 'upload',
    reload: author && access.can('patchKnowledgeSpecification') && module.source?.kind === 'git',
    delete: !federated && mayDelete(access, 'deleteKnowledgeSpecification', module.lifecycleStatus),
  }
}
