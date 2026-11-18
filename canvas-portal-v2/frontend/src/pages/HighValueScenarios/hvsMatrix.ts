/**
 * AN L4 high-value scenarios for 2025-2027 (reference map).
 *
 * Each matrix crosses lifecycle scenarios (rows) with product, service or
 * network domains (columns). A cell is either out of scope, an AN L4 high-value
 * scenario, or a high-value scenario that also has an ANL evaluation.
 */

export type HvsMark = 'none' | 'hvs' | 'hvs-anl'

export interface HvsCell {
  mark: HvsMark
  /** Number of domain columns the cell covers. Defaults to 1. */
  span?: number
}

export interface HvsColumn {
  key: string
  label: string
  /** Set on the placeholder columns that stand for further domains. */
  placeholder?: boolean
}

export interface HvsColumnGroup {
  /** Omitted when the column has no parent group. */
  label?: string
  columns: HvsColumn[]
}

export interface HvsRow {
  key: string
  label: string
  cells: HvsCell[]
}

export interface HvsStage {
  key: string
  label: string
  rows: HvsRow[]
}

export interface HvsMatrix {
  key: string
  title: string
  columnGroups: HvsColumnGroup[]
  stages: HvsStage[]
}

const _: HvsCell = { mark: 'none' }
const H: HvsCell = { mark: 'hvs' }
const A: HvsCell = { mark: 'hvs-anl' }
const span = (cell: HvsCell, n: number): HvsCell => ({ ...cell, span: n })

export const businessMatrix: HvsMatrix = {
  key: 'business',
  title: 'Business-oriented',
  columnGroups: [
    { label: 'Individual', columns: [{ key: 'individual-sla', label: 'SLA-guaranteed Products' }] },
    {
      label: 'Home',
      columns: [
        { key: 'home-sla', label: 'SLA-guaranteed Products' },
        { key: 'smart-home', label: 'Smart Home' },
      ],
    },
    {
      label: 'Enterprise',
      columns: [
        { key: 'enterprise-sla', label: 'SLA-guaranteed Products' },
        { key: 'highly-secure', label: 'Highly Secure Products' },
        { key: 'slicing-private-line', label: 'Slicing Private Line' },
        { key: 'naas', label: 'NaaS Products' },
      ],
    },
  ],
  stages: [
    {
      key: 'business-innovation',
      label: 'Business Innovation',
      rows: [
        { key: 'product-definition', label: 'Product definition', cells: [_, _, H, H, _, _, _] },
        { key: 'product-marketing', label: 'Product marketing', cells: [_, _, _, _, _, _, _] },
        { key: 'experience-management', label: 'Experience management', cells: [H, H, _, _, _, _, _] },
      ],
    },
  ],
}

export const serviceMatrix: HvsMatrix = {
  key: 'service',
  title: 'Service-oriented',
  columnGroups: [
    { label: 'Individual Services', columns: [{ key: '4g5g', label: '4G/5G' }] },
    {
      label: 'Home Services',
      columns: [
        { key: 'home-broadband', label: 'Home Broadband' },
        { key: 'home-other', label: 'Other home services', placeholder: true },
      ],
    },
    {
      label: 'Enterprise Services',
      columns: [
        { key: 'private-line', label: 'Private line' },
        { key: '5g2b', label: '5G2B' },
        { key: 'iot', label: 'IoT' },
        { key: 'enterprise-other', label: 'Other enterprise services', placeholder: true },
      ],
    },
  ],
  stages: [
    {
      key: 'operation',
      label: 'Operation',
      rows: [
        { key: 'service-marketing', label: 'Service marketing', cells: [_, _, _, _, _, _, _] },
        { key: 'service-delivery', label: 'Service delivery', cells: [_, _, _, H, H, _, _] },
        { key: 'service-assurance', label: 'Service assurance', cells: [H, H, _, H, span(H, 2), _] },
        { key: 'complaint-handling', label: 'Complaint handling', cells: [H, H, _, H, span(H, 2), _] },
      ],
    },
  ],
}

export const networkMatrix: HvsMatrix = {
  key: 'network',
  title: 'Network-oriented',
  columnGroups: [
    {
      columns: [
        { key: 'wireless', label: 'Wireless network' },
        { key: 'core', label: 'Core network' },
        { key: 'fixed-access', label: 'Fixed access network' },
        { key: 'transport', label: 'Transport network' },
        { key: 'ip', label: 'IP network' },
        { key: 'others', label: 'Others (e.g. Cloud)' },
      ],
    },
  ],
  stages: [
    {
      key: 'plan',
      label: 'Plan',
      rows: [{ key: 'network-planning', label: 'Network planning', cells: [_, _, _, _, _, _] }],
    },
    {
      key: 'deployment',
      label: 'Deployment',
      rows: [{ key: 'network-deployment', label: 'Network deployment', cells: [_, _, _, _, _, _] }],
    },
    {
      key: 'maintenance',
      label: 'Maintenance',
      rows: [
        { key: 'fault-management', label: 'Fault management', cells: [A, A, _, H, A, _] },
        { key: 'network-change', label: 'Network change', cells: [_, A, _, H, H, _] },
      ],
    },
    {
      key: 'optimization',
      label: 'Optimization',
      rows: [
        { key: 'quality-optimization', label: 'Quality optimization', cells: [H, _, _, H, A, _] },
        { key: 'energy-efficiency', label: 'Energy efficiency optimization', cells: [H, _, _, _, _, _] },
        { key: 'resource-management', label: 'Resource management', cells: [_, _, _, _, _, _] },
      ],
    },
  ],
}

export const hvsMatrices: HvsMatrix[] = [businessMatrix, serviceMatrix, networkMatrix]

