import React from 'react'
import { ThunderboltOutlined } from '@ant-design/icons'
import SectionedPage, { PageSection } from '@/components/SectionedPage'
import { colors } from '@/theme'
import { HvsLegend, HvsMatrixTable } from './HvsMatrixTable'
import { hvsMatrices } from './hvsMatrix'

const allCells = hvsMatrices.flatMap((m) => m.stages.flatMap((s) => s.rows.flatMap((r) => r.cells)))
const hvsCount = allCells.filter((c) => c.mark !== 'none').length
const anlCount = allCells.filter((c) => c.mark === 'hvs-anl').length

function AutonomousFlows() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 650, color: colors.textPrimary }}>
          AN L4 high-value scenarios in 2025–2027{' '}
          <span style={{ fontWeight: 400, color: colors.textMuted }}>(reference)</span>
        </div>
        <HvsLegend hvsCount={hvsCount} anlCount={anlCount} />
      </div>
      {hvsMatrices.map((matrix) => (
        <HvsMatrixTable key={matrix.key} matrix={matrix} />
      ))}
    </div>
  )
}

const sections: PageSection[] = [
  {
    key: 'autonomous-flows',
    title: 'Autonomous Flows',
    description: 'End-to-end flows executed autonomously by agents.',
    content: <AutonomousFlows />,
  },
  { key: 'solution-packages', title: 'Solution Packages', description: 'Packaged solutions bundling components, agents and flows.' },
]

export default function HighValueScenariosPage() {
  return (
    <SectionedPage
      title="High Value Scenarios"
      description="Autonomous flows and solution packages"
      icon={<ThunderboltOutlined />}
      sections={sections}
    />
  )
}
