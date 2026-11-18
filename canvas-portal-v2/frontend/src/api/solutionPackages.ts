import apiClient from './client'

export interface SolutionPackageTask {
  agent: string
  skill: string
  description: string
}

export interface SolutionPackageStep {
  name: string
  type: string
  next: string[]
  tasks: SolutionPackageTask[]
}

/** A PSOP workflow tagged as a solution package in the OpenAN Orchestration Center. */
export interface SolutionPackage {
  id: string
  name: string
  summary: string
  createdAt: string | null
  tags: string[]
  agents: string[]
  skills: string[]
  steps: SolutionPackageStep[]
}

export interface SolutionPackageList {
  /** False when the portal has no Orchestration Center configured. */
  configured: boolean
  /** The Orchestration Center's workflow viewer, when configured. */
  pageUrl: string | null
  fetchedAt: string | null
  packages: SolutionPackage[]
}

export async function fetchSolutionPackages(): Promise<SolutionPackageList> {
  const { data } = await apiClient.get<SolutionPackageList>('/api/solution-packages')
  return data
}
