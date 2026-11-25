import React, { useState } from 'react'
import { Input, Upload, message } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { colors } from '@/theme'

// The portal's limit for API bodies other than document uploads.
const MAX_BYTES = 10 * 1024 * 1024

interface TurtleInputProps {
  value?: string
  onChange?: (value: string) => void
  accept?: string
  placeholder?: string
  label?: string
}

/** RDF text, from a file or typed. Works as a Form.Item control. */
export default function TurtleInput({
  value,
  onChange,
  accept = '.ttl,.turtle,text/turtle',
  placeholder = '@prefix ex: <https://example.org/ns#> .',
  label = 'Turtle content',
}: TurtleInputProps) {
  const [file, setFile] = useState<string>()
  return (
    <div>
      <Upload.Dragger
        accept={accept}
        maxCount={1}
        showUploadList={false}
        beforeUpload={(chosen) => {
          if (chosen.size > MAX_BYTES) {
            message.error(`${chosen.name} is larger than 10 MB`)
          } else {
            chosen.text().then(
              (text) => {
                setFile(chosen.name)
                onChange?.(text)
              },
              () => message.error(`${chosen.name} can't be read`),
            )
          }
          return false
        }}
        style={{ marginBottom: 8 }}
      >
        <div style={{ padding: '4px 0', fontSize: 12, color: colors.textSecondary }}>
          <InboxOutlined style={{ fontSize: 18, marginRight: 8 }} />
          {file ? `${file} — choose or drop another file to replace it` : 'Choose or drop a file, or paste the text below'}
        </div>
      </Upload.Dragger>
      <Input.TextArea
        aria-label={label}
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        placeholder={placeholder}
        autoSize={{ minRows: 6, maxRows: 14 }}
        spellCheck={false}
        style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
      />
    </div>
  )
}
