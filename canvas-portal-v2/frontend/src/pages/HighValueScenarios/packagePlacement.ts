import type { SolutionPackage } from '@/api/solutionPackages'
import { hvsMatrices, type HvsMark, type HvsMatrix } from './hvsMatrix'

/**
 * Where a solution package sits on the high-value scenario map.
 *
 * The Orchestration Center has no scenario field, so this is derived from the
 * package name, which follows the TM Forum pattern
 * `IG1503B_AN_L4_Home_Broadband_Service_Assurance_Solution_Package`.
 */
export interface PackagePlacement {
  /** TM Forum IG document code, e.g. IG1503B. */
  code: string | null
  /** The name without its code, prefix and suffix, in words. */
  title: string
  matrixKey: string | null
  stageLabel: string | null
  rowKey: string | null
  rowLabel: string | null
  columnKey: string | null
  columnLabel: string | null
}

const SCENARIO_KEYWORDS: Array<[RegExp, string]> = [
  [/fault management/, 'fault-management'],
  [/service assurance/, 'service-assurance'],
  [/complaint handling/, 'complaint-handling'],
  [/service delivery/, 'service-delivery'],
  [/marketing/, 'service-marketing'],
  [/planning/, 'network-planning'],
  [/deployment/, 'network-deployment'],
  [/change management|network change/, 'network-change'],
  [/quality optimi[sz]ation/, 'quality-optimization'],
  [/energy efficiency/, 'energy-efficiency'],
  [/resource management/, 'resource-management'],
]

/** Domain keywords per matrix, so "mobile" means 4G/5G for services and wireless for networks. */
const DOMAIN_KEYWORDS: Record<string, Array<[RegExp, string]>> = {
  service: [
    [/private line/, 'private-line'],
    [/5g2b/, '5g2b'],
    [/\biot\b/, 'iot'],
    [/home broadband/, 'home-broadband'],
    [/individual|4g|5g|mobile/, '4g5g'],
  ],
  network: [
    [/wireless|mobile|\bran\b/, 'wireless'],
    [/core network/, 'core'],
    [/fixed access/, 'fixed-access'],
    [/transport/, 'transport'],
    [/\bip network\b/, 'ip'],
    [/computing|cloud/, 'others'],
  ],
}

function words(name: string): { code: string | null; title: string } {
  const base = name.replace(/_v\d+(\.\d+)*$/i, '')
  const match = /^(IG\d+[A-Z]?)_(?:AN_L4_)?(.*)$/i.exec(base)
  const rest = (match ? match[2] : base).replace(/_?Solution_?Package$/i, '')
  const title = rest
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  return { code: match ? match[1].toUpperCase() : null, title: title || name }
}

function findRow(rowKey: string): { matrix: HvsMatrix; stageLabel: string; rowLabel: string } | null {
  for (const matrix of hvsMatrices) {
    for (const stage of matrix.stages) {
      const row = stage.rows.find((r) => r.key === rowKey)
      if (row) return { matrix, stageLabel: stage.label, rowLabel: row.label }
    }
  }
  return null
}

export function placePackage(pkg: SolutionPackage): PackagePlacement {
  const { code, title } = words(pkg.name)
  const text = title.toLowerCase()
  const placement: PackagePlacement = {
    code,
    title,
    matrixKey: null,
    stageLabel: null,
    rowKey: null,
    rowLabel: null,
    columnKey: null,
    columnLabel: null,
  }

  const rowKey = SCENARIO_KEYWORDS.find(([re]) => re.test(text))?.[1]
  const row = rowKey ? findRow(rowKey) : null
  if (!rowKey || !row) return placement
  Object.assign(placement, { matrixKey: row.matrix.key, stageLabel: row.stageLabel, rowKey, rowLabel: row.rowLabel })

  const columnKey = DOMAIN_KEYWORDS[row.matrix.key]?.find(([re]) => re.test(text))?.[1]
  const column = row.matrix.columnGroups.flatMap((g) => g.columns).find((c) => c.key === columnKey)
  if (column) Object.assign(placement, { columnKey: column.key, columnLabel: column.label })
  return placement
}

export interface PlacedPackage {
  pkg: SolutionPackage
  placement: PackagePlacement
}

export function cellKey(rowKey: string, columnKey: string): string {
  return `${rowKey}:${columnKey}`
}

/** Packages grouped by the map cell they belong to. Packages without a domain are left out. */
export function packagesByCell(placed: PlacedPackage[]): Map<string, PlacedPackage[]> {
  const cells = new Map<string, PlacedPackage[]>()
  for (const item of placed) {
    const { rowKey, columnKey } = item.placement
    if (!rowKey || !columnKey) continue
    const key = cellKey(rowKey, columnKey)
    cells.set(key, [...(cells.get(key) ?? []), item])
  }
  return cells
}

/** The map's mark for a cell, following merged cells. Null when the package has no cell. */
export function markFor(placement: PackagePlacement): HvsMark | null {
  const { matrixKey, rowKey, columnKey } = placement
  const matrix = hvsMatrices.find((m) => m.key === matrixKey)
  if (!matrix || !rowKey || !columnKey) return null
  const row = matrix.stages.flatMap((s) => s.rows).find((r) => r.key === rowKey)
  const index = matrix.columnGroups.flatMap((g) => g.columns).findIndex((c) => c.key === columnKey)
  if (!row || index < 0) return null
  let start = 0
  for (const cell of row.cells) {
    const end = start + (cell.span ?? 1)
    if (index < end) return cell.mark
    start = end
  }
  return null
}
