import React from 'react'
import {
  CompressOutlined,
  DownloadOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  MinusOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import { Button, Dropdown, Space } from 'antd'

interface GraphToolbarProps {
  exports: { key: string; label: string; onClick: () => void }[]
  exportDisabled: boolean
  isFullscreen: boolean
  onToggleFullscreen: () => void
  onZoom: (factor: number) => void
  onFit: () => void
}

/** Export, fullscreen and zoom buttons of a graph. */
export default function GraphToolbar({ exports, exportDisabled, isFullscreen, onToggleFullscreen, onZoom, onFit }: GraphToolbarProps) {
  return (
    <Space size={8} wrap>
      <Dropdown disabled={exportDisabled} menu={{ items: exports }}>
        <Button icon={<DownloadOutlined />}>Export</Button>
      </Dropdown>
      <Button icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />} onClick={onToggleFullscreen}>
        {isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
      </Button>
      <Space.Compact>
        <Button aria-label="Zoom out" icon={<MinusOutlined />} onClick={() => onZoom(0.82)} />
        <Button icon={<CompressOutlined />} onClick={onFit}>Fit</Button>
        <Button aria-label="Zoom in" icon={<PlusOutlined />} onClick={() => onZoom(1.22)} />
      </Space.Compact>
    </Space>
  )
}
