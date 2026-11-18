import React, { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Select } from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'
import SectionedPage, { PageSection } from '@/components/SectionedPage'
import { fetchSolutionPackages } from '@/api/solutionPackages'
import { colors } from '@/theme'
import { ExperienceLegend, HvsLegend, HvsMatrixTable, type CellSelection } from './HvsMatrixTable'
import { hvsExperiences, hvsMatrices } from './hvsMatrix'
import { packagesByCell, placePackage, type PlacedPackage } from './packagePlacement'
import SolutionPackagesTable from './SolutionPackagesTable'

const allCells = hvsMatrices.flatMap((m) => m.stages.flatMap((s) => s.rows.flatMap((r) => r.cells)))
const hvsCount = allCells.filter((c) => c.mark !== 'none').length
const anlCount = allCells.filter((c) => c.mark === 'hvs-anl').length
const experienceOptions = [
  { value: '', label: <span style={{ color: colors.textMuted }}>None</span> },
  ...hvsExperiences.map((e) => ({ value: e.key, label: e.label })),
]

function selectionLabel(selection: CellSelection): string {
  for (const matrix of hvsMatrices) {
    const row = matrix.stages.flatMap((s) => s.rows).find((r) => r.key === selection.rowKey)
    if (!row) continue
    const columns = matrix.columnGroups.flatMap((g) => g.columns)
    const labels = selection.columnKeys.map((key) => columns.find((c) => c.key === key)?.label ?? key)
    return `${row.label} × ${labels.join(' + ')}`
  }
  return selection.rowKey
}

interface AutonomousFlowsProps {
  packages: Map<string, PlacedPackage[]>
  packageCount: number
  selected: CellSelection | null
  onSelect: (selection: CellSelection | null) => void
}

function AutonomousFlows({ packages, packageCount, selected, onSelect }: AutonomousFlowsProps) {
  const [experienceKey, setExperienceKey] = useState('')
  const experience = hvsExperiences.find((e) => e.key === experienceKey) ?? null

  function chooseExperience(key: string) {
    setExperienceKey(key)
    // The map cells cannot be clicked while an experience is shown, so drop the package filter.
    if (key) onSelect(null)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <label style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, fontSize: 12, color: colors.textSecondary }}>
        Experience
        <Select
          value={experienceKey}
          onChange={chooseExperience}
          options={experienceOptions}
          style={{ width: 280, maxWidth: '100%' }}
          aria-label="Experience"
        />
      </label>
      {hvsMatrices.map((matrix) => (
        <HvsMatrixTable
          key={matrix.key}
          matrix={matrix}
          packages={packages}
          selected={selected}
          onSelect={onSelect}
          experience={experience}
        />
      ))}
      {experience ? (
        <ExperienceLegend experience={experience} />
      ) : (
        <HvsLegend hvsCount={hvsCount} anlCount={anlCount} packageCount={packageCount} />
      )}
    </div>
  )
}

export default function HighValueScenariosPage() {
  const query = useQuery({
    queryKey: ['solution-packages'],
    queryFn: fetchSolutionPackages,
    refetchInterval: 60_000,
    retry: 1,
  })
  const [selected, setSelected] = useState<CellSelection | null>(null)

  const placed = useMemo<PlacedPackage[]>(
    () => (query.data?.packages ?? []).map((pkg) => ({ pkg, placement: placePackage(pkg) })),
    [query.data],
  )
  const byCell = useMemo(() => packagesByCell(placed), [placed])
  const placedInCells = useMemo(() => [...byCell.values()].reduce((n, list) => n + list.length, 0), [byCell])
  const visible = useMemo(
    () =>
      selected
        ? placed.filter(
            ({ placement }) =>
              placement.rowKey === selected.rowKey &&
              !!placement.columnKey &&
              selected.columnKeys.includes(placement.columnKey),
          )
        : placed,
    [placed, selected],
  )

  function select(selection: CellSelection | null) {
    setSelected(selection)
    if (selection) {
      document.getElementById('solution-packages')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  const sections: PageSection[] = [
    {
      key: 'autonomous-flows',
      title: 'Autonomous Flows',
      description: 'End-to-end flows executed autonomously by agents.',
      content: (
        <AutonomousFlows packages={byCell} packageCount={placedInCells} selected={selected} onSelect={select} />
      ),
    },
    {
      key: 'solution-packages',
      title: 'Solution Packages',
      description: 'Packaged solutions bundling components, agents and flows.',
      content: (
        <div id="solution-packages" style={{ scrollMarginTop: 16 }}>
          <SolutionPackagesTable
            data={query.data}
            placed={visible}
            loading={query.isLoading}
            error={query.error}
            filterLabel={selected ? selectionLabel(selected) : null}
            onClearFilter={() => setSelected(null)}
          />
        </div>
      ),
    },
  ]

  return (
    <SectionedPage
      title="High Value Scenarios"
      description="Autonomous flows and solution packages"
      icon={<ThunderboltOutlined />}
      sections={sections}
    />
  )
}
