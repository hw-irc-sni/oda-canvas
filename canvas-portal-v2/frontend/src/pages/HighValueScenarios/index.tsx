import React, { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ThunderboltOutlined } from '@ant-design/icons'
import SectionedPage, { PageSection } from '@/components/SectionedPage'
import { fetchSolutionPackages } from '@/api/solutionPackages'
import { colors } from '@/theme'
import { HvsLegend, HvsMatrixTable, type CellSelection } from './HvsMatrixTable'
import { hvsMatrices } from './hvsMatrix'
import { packagesByCell, placePackage, type PlacedPackage } from './packagePlacement'
import SolutionPackagesTable from './SolutionPackagesTable'

const allCells = hvsMatrices.flatMap((m) => m.stages.flatMap((s) => s.rows.flatMap((r) => r.cells)))
const hvsCount = allCells.filter((c) => c.mark !== 'none').length
const anlCount = allCells.filter((c) => c.mark === 'hvs-anl').length

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
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 650, color: colors.textPrimary }}>
          AN L4 high-value scenarios in 2025–2027{' '}
          <span style={{ fontWeight: 400, color: colors.textMuted }}>(reference)</span>
        </div>
        <HvsLegend hvsCount={hvsCount} anlCount={anlCount} packageCount={packageCount} />
      </div>
      {hvsMatrices.map((matrix) => (
        <HvsMatrixTable key={matrix.key} matrix={matrix} packages={packages} selected={selected} onSelect={onSelect} />
      ))}
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
