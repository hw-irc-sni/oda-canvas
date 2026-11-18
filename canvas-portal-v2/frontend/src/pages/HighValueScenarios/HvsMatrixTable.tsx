import React from 'react'
import { Tooltip } from 'antd'
import { AuditOutlined } from '@ant-design/icons'
import { colors } from '@/theme'
import type { HvsCell, HvsColumn, HvsMatrix } from './hvsMatrix'

const hvsColor = colors.statusDown
const hvsBg = colors.statusDownBg
const stageColor = colors.tmfText
const stageBg = colors.tmfBg

const cellPad = 4
const tableStyle: React.CSSProperties = {
  width: '100%',
  minWidth: 760,
  borderCollapse: 'separate',
  borderSpacing: cellPad,
  tableLayout: 'fixed',
  fontSize: 12,
}

const headerBase: React.CSSProperties = {
  background: colors.surfaceMuted,
  color: colors.textPrimary,
  borderRadius: 6,
  padding: '6px 8px',
  textAlign: 'center',
  fontWeight: 600,
  lineHeight: 1.25,
}

function HvsDot() {
  return (
    <span
      aria-hidden
      style={{
        width: 10,
        height: 10,
        borderRadius: '50%',
        border: `2px solid ${hvsColor}`,
        background: colors.bgCard,
        display: 'inline-block',
      }}
    />
  )
}

function EmptyDot() {
  return (
    <span
      aria-hidden
      style={{
        width: 8,
        height: 8,
        borderRadius: '50%',
        border: `1.5px solid ${colors.textMuted}`,
        opacity: 0.5,
        display: 'inline-block',
      }}
    />
  )
}

function AnlIcon() {
  return <AuditOutlined aria-hidden style={{ fontSize: 15, color: colors.primary }} />
}

function columnLabels(columns: HvsColumn[]): string {
  return columns.map((c) => (c.placeholder ? c.label.toLowerCase() : c.label)).join(' + ')
}

function describe(cell: HvsCell, row: string, columns: HvsColumn[]): string {
  const where = `${row} × ${columnLabels(columns)}`
  if (cell.mark === 'hvs-anl') return `${where}: AN L4 high-value scenario, with ANL evaluation`
  if (cell.mark === 'hvs') return `${where}: AN L4 high-value scenario`
  return `${where}: not a high-value scenario`
}

function MatrixCell({ cell, row, columns }: { cell: HvsCell; row: string; columns: HvsColumn[] }) {
  const label = describe(cell, row, columns)
  const highlighted = cell.mark !== 'none'
  return (
    <td colSpan={cell.span ?? 1} style={{ padding: 0 }}>
      <Tooltip title={label} mouseEnterDelay={0.2}>
        <div
          role="img"
          aria-label={label}
          style={{
            height: 34,
            borderRadius: 6,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            border: highlighted ? `1.5px solid ${hvsColor}` : `1px dashed ${colors.border}`,
            background: highlighted ? hvsBg : colors.surfaceSubtle,
          }}
        >
          {cell.mark === 'hvs-anl' && <AnlIcon />}
          {highlighted ? <HvsDot /> : <EmptyDot />}
        </div>
      </Tooltip>
    </td>
  )
}

function ColumnHeader({ column }: { column: HvsColumn }) {
  if (column.placeholder) {
    return (
      <th style={{ ...headerBase, fontWeight: 400, color: colors.textMuted }}>
        <Tooltip title={column.label}>
          <span aria-label={column.label}>…</span>
        </Tooltip>
      </th>
    )
  }
  return <th style={{ ...headerBase, fontWeight: 500, color: colors.textSecondary }}>{column.label}</th>
}

export function HvsMatrixTable({ matrix }: { matrix: HvsMatrix }) {
  const columns = matrix.columnGroups.flatMap((g) => g.columns)
  const grouped = matrix.columnGroups.some((g) => g.label)
  const stageWidth = 104
  const rowWidth = 200

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={tableStyle} aria-label={`${matrix.title} high-value scenarios`}>
        <colgroup>
          <col style={{ width: stageWidth }} />
          <col style={{ width: rowWidth }} />
          {columns.map((c) => (
            <col key={c.key} style={{ width: c.placeholder ? 44 : undefined }} />
          ))}
        </colgroup>
        <thead>
          <tr>
            <th
              colSpan={2}
              rowSpan={grouped ? 2 : 1}
              style={{ ...headerBase, fontSize: 13, fontWeight: 700, background: colors.surfaceInsetStrong }}
            >
              {matrix.title}
            </th>
            {grouped
              ? matrix.columnGroups.map((g, i) => (
                  <th key={g.label ?? i} colSpan={g.columns.length} style={{ ...headerBase, background: colors.surfaceInsetStrong }}>
                    {g.label}
                  </th>
                ))
              : columns.map((c) => (
                  <th key={c.key} style={{ ...headerBase, background: colors.surfaceInsetStrong }}>
                    {c.label}
                  </th>
                ))}
          </tr>
          {grouped && (
            <tr>
              {columns.map((c) => (
                <ColumnHeader key={c.key} column={c} />
              ))}
            </tr>
          )}
        </thead>
        <tbody>
          {matrix.stages.map((stage) =>
            stage.rows.map((row, rowIndex) => {
              let col = 0
              return (
                <tr key={row.key}>
                  {rowIndex === 0 && (
                    <th
                      scope="rowgroup"
                      rowSpan={stage.rows.length}
                      style={{
                        background: stageBg,
                        color: stageColor,
                        border: `1px solid ${hvsColor}33`,
                        borderRadius: 6,
                        padding: '6px 8px',
                        fontWeight: 700,
                        textAlign: 'center',
                      }}
                    >
                      {stage.label}
                    </th>
                  )}
                  <th
                    scope="row"
                    style={{
                      background: colors.surfaceSelected,
                      color: colors.textPrimary,
                      borderRadius: 6,
                      padding: '6px 10px',
                      fontWeight: 500,
                      textAlign: 'left',
                      lineHeight: 1.25,
                    }}
                  >
                    {row.label}
                  </th>
                  {row.cells.map((cell) => {
                    const covered = columns.slice(col, col + (cell.span ?? 1))
                    col += cell.span ?? 1
                    return <MatrixCell key={covered[0].key} cell={cell} row={row.label} columns={covered} />
                  })}
                </tr>
              )
            }),
          )}
        </tbody>
      </table>
    </div>
  )
}

export function HvsLegend({ hvsCount, anlCount }: { hvsCount: number; anlCount: number }) {
  const item: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 8 }
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: '8px 24px',
        fontSize: 12,
        color: colors.textSecondary,
      }}
    >
      <span style={item}>
        <HvsDot />
        AN L4 high-value scenarios 2025–2027 ({hvsCount})
      </span>
      <span style={item}>
        <AnlIcon />
        ANL evaluation ({anlCount})
      </span>
      <span style={item}>
        <EmptyDot />
        Not targeted
      </span>
    </div>
  )
}
