import React from 'react'
import { Col, Form, Input, Row, Select, type FormInstance } from 'antd'
import type { KnowledgeSpecification, KnowledgeSpecificationInput, OntologyLayer, SpecKind } from '@/api/knowledge'
import { LAYERS } from '../shared'
import { moduleName, resolveRef } from './modules'

export interface ModuleFields {
  name: string
  iri: string
  prefix?: string
  version?: string
  layer?: OntologyLayer
  specKind: SpecKind
  depth?: string
  description?: string
  imports?: string[]
  describes?: string[]
}

const KIND_OPTIONS: { value: SpecKind; label: string }[] = [
  { value: 'ontology', label: 'Ontology (classes and properties)' },
  { value: 'vocabulary', label: 'Vocabulary (SKOS concepts)' },
  { value: 'shapes', label: 'Shapes (SHACL)' },
  { value: 'bridge', label: 'Bridge (mappings between modules)' },
]

const blank = (value: string | undefined) => value?.trim() || undefined

/** The fields as the API takes them, without the empty ones. */
export function toInput(fields: ModuleFields): Omit<KnowledgeSpecificationInput, 'source'> {
  return {
    name: fields.name.trim(),
    iri: fields.iri.trim(),
    prefix: blank(fields.prefix),
    version: blank(fields.version),
    layer: fields.layer,
    specKind: fields.specKind,
    depth: blank(fields.depth),
    description: blank(fields.description),
    imports: fields.imports?.length ? fields.imports.map((id) => ({ id })) : undefined,
    describes: fields.specKind === 'vocabulary' && fields.describes?.length ? fields.describes.map((id) => ({ id })) : undefined,
  }
}

export function toFields(module: KnowledgeSpecification, modules: KnowledgeSpecification[]): ModuleFields {
  const ids = (refs: KnowledgeSpecification['imports']) =>
    (refs ?? []).map((ref) => resolveRef(ref, modules)?.id).filter((id): id is string => !!id)
  return {
    name: module.name ?? '',
    iri: module.iri ?? '',
    prefix: module.prefix,
    version: module.version,
    layer: module.layer,
    specKind: module.specKind ?? 'ontology',
    depth: module.depth,
    description: module.description,
    imports: ids(module.imports),
    describes: ids(module.describes),
  }
}

/** The metadata fields of a module; inside an antd Form. */
export default function ModuleFormFields({
  form,
  modules,
  editing,
}: {
  form: FormInstance
  modules: KnowledgeSpecification[]
  /** The module being edited: its IRI can't change. */
  editing?: KnowledgeSpecification
}) {
  const kind = Form.useWatch('specKind', form) as SpecKind | undefined
  const options = modules
    .filter((m) => m.id !== editing?.id)
    .map((m) => ({ value: m.id, label: moduleName(m) }))
    .sort((a, b) => a.label.localeCompare(b.label))
  return (
    <>
      <Row gutter={12}>
        <Col span={12}>
          <Form.Item label="Name" name="name" rules={[{ required: true, whitespace: true, message: 'Name is required' }]}>
            <Input placeholder="Network" />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item
            label="Ontology IRI"
            name="iri"
            tooltip="Unique on the instance; owl:imports are resolved with it. It can't change later."
            rules={[
              { required: true, whitespace: true, message: 'The IRI is required' },
              { pattern: /^\s*[A-Za-z][A-Za-z0-9+.-]*:[^\s<>"{}|^`\\]+\s*$/, message: 'Enter an absolute IRI' },
            ]}
          >
            <Input disabled={!!editing} placeholder="https://example.org/network" />
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item label="Kind" name="specKind" rules={[{ required: true, message: 'Choose a kind' }]}>
            <Select options={KIND_OPTIONS} popupMatchSelectWidth={false} />
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item label="Layer" name="layer">
            {/* The API can't remove a layer once it is set. */}
            <Select allowClear={!editing?.layer} options={LAYERS.map((value) => ({ value, label: value }))} placeholder="None" />
          </Form.Item>
        </Col>
        <Col span={4}>
          <Form.Item label="Prefix" name="prefix"><Input placeholder="net" /></Form.Item>
        </Col>
        <Col span={4}>
          <Form.Item label="Version" name="version"><Input placeholder="1.0" /></Form.Item>
        </Col>
        <Col span={24}>
          <Form.Item label="Description" name="description"><Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} /></Form.Item>
        </Col>
        <Col span={16}>
          <Form.Item label="Imports" name="imports" tooltip="owl:imports found in the content are added by the component.">
            <Select mode="multiple" allowClear optionFilterProp="label" options={options} placeholder="Registered modules" />
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item label="Depth" name="depth"><Input placeholder="Full ontology" /></Form.Item>
        </Col>
        {kind === 'vocabulary' && (
          <Col span={24}>
            <Form.Item label="Describes" name="describes" tooltip="The modules whose terms this vocabulary describes.">
              <Select mode="multiple" allowClear optionFilterProp="label" options={options} placeholder="Registered modules" />
            </Form.Item>
          </Col>
        )}
      </Row>
    </>
  )
}
