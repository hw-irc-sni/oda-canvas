import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { isRunning, knowledgeScopeKey, useProcesses, type Process, type ProcessState, type ProcessTask } from '@/api/knowledge'
import { useKnowledge } from '../KnowledgeContext'

export const STEPS: { step: NonNullable<ProcessTask['step']>; label: string }[] = [
  { step: 'KM-FX-01', label: 'Data processing' },
  { step: 'KM-FX-02', label: 'Information extraction' },
  { step: 'KM-FX-03', label: 'Knowledge generation' },
  { step: 'KM-FX-04', label: 'Knowledge fusion' },
  { step: 'KM-FX-05', label: 'Knowledge storage' },
  { step: 'KM-FX-06', label: 'Knowledge recommendation' },
]

export const STATE_COLORS: Record<ProcessState, string> = {
  acknowledged: 'default',
  inProgress: 'processing',
  completed: 'success',
  failed: 'error',
}

export const STATE_LABELS: Record<ProcessState, string> = {
  acknowledged: 'Waiting',
  inProgress: 'Running',
  completed: 'Completed',
  failed: 'Failed',
}

/** The ingestion processes of a knowledge-base; when they finish, what they changed is read again. */
export function useBaseProcesses(id: string): { processes: Process[]; running: boolean; query: ReturnType<typeof useProcesses> } {
  const { instance, can } = useKnowledge()
  const queryClient = useQueryClient()
  const query = useProcesses(instance, { enabled: can('listProcess') })
  const processes = (query.data ?? []).filter((process) => process.knowledge?.id === id)
  const running = processes.some((process) => isRunning(process.state))
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !running) queryClient.invalidateQueries({ queryKey: knowledgeScopeKey(instance) })
    wasRunning.current = running
  }, [running, instance, queryClient])
  return { processes, running, query }
}
