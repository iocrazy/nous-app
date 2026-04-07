import { useState, useCallback } from 'react'
import {
  Button,
  Space,
  Dropdown,
  Menu,
  DatePicker,
  Tag,
} from '@arco-design/web-react'
import {
  IconMore,
  IconRefresh,
  IconDownload,
  IconPlayArrow,
  IconPause,
  IconStop,
  IconCalendar,
} from '@arco-design/web-react/icon'

// --- Types ---

interface TimeRange {
  label: string
  value: string
  ms: number
}

const TIME_RANGES: TimeRange[] = [
  { label: '15m', value: '15m', ms: 15 * 60 * 1000 },
  { label: '1h', value: '1h', ms: 60 * 60 * 1000 },
  { label: '24h', value: '24h', ms: 24 * 60 * 60 * 1000 },
  { label: '3d', value: '3d', ms: 3 * 24 * 60 * 60 * 1000 },
  { label: '7d', value: '7d', ms: 7 * 24 * 60 * 60 * 1000 },
]

interface LogsToolbarExtraProps {
  // Time range
  selectedRange: string
  onRangeChange: (range: string, startDate: string) => void
  onCustomRange: (start: string, end: string) => void

  // Live mode (optional — only for Application Logs)
  showLive?: boolean
  liveTail?: boolean
  livePaused?: boolean
  liveCount?: number
  onLiveStart?: () => void
  onLiveStop?: () => void
  onLivePause?: () => void

  // Actions
  onRefresh: () => void
  onExportCsv: () => void
  onExportJson: () => void
}

export function LogsToolbarExtra({
  selectedRange,
  onRangeChange,
  onCustomRange,
  showLive = false,
  liveTail = false,
  livePaused = false,
  liveCount = 0,
  onLiveStart,
  onLiveStop,
  onLivePause,
  onRefresh,
  onExportCsv,
  onExportJson,
}: LogsToolbarExtraProps) {
  const [showDatePicker, setShowDatePicker] = useState(false)

  const handleRangeClick = useCallback(
    (range: TimeRange) => {
      const startDate = new Date(Date.now() - range.ms).toISOString()
      onRangeChange(range.value, startDate)
    },
    [onRangeChange],
  )

  const moreMenu = (
    <Menu>
      <Menu.Item key="refresh" onClick={onRefresh}>
        <Space size={8}>
          <IconRefresh />
          Refresh
        </Space>
      </Menu.Item>
      <hr style={{ margin: '4px 0', border: 'none', borderTop: '1px solid var(--color-fill-3)' }} />
      <Menu.Item key="json" onClick={onExportJson}>
        <Space size={8}>
          <IconDownload />
          Download as JSON
        </Space>
      </Menu.Item>
      <Menu.Item key="csv" onClick={onExportCsv}>
        <Space size={8}>
          <IconDownload />
          Download as CSV
        </Space>
      </Menu.Item>
    </Menu>
  )

  return (
    <Space size={8} style={{ display: 'flex', alignItems: 'center' }}>
      {/* Live controls (only for Application Logs) */}
      {showLive && !liveTail && (
        <Button
          size="small"
          type="outline"
          icon={<IconPlayArrow />}
          onClick={onLiveStart}
        >
          Live
        </Button>
      )}
      {showLive && liveTail && (
        <>
          <Button
            size="small"
            type={livePaused ? 'primary' : 'outline'}
            icon={livePaused ? <IconPlayArrow /> : <IconPause />}
            onClick={onLivePause}
          />
          <Button
            size="small"
            type="outline"
            status="danger"
            icon={<IconStop />}
            onClick={onLiveStop}
          >
            Stop
          </Button>
          <Tag color="green" size="small">{liveCount} entries</Tag>
        </>
      )}

      {/* Time range selector (hidden during live mode) */}
      {!liveTail && (
        <>
          <div
            style={{
              display: 'inline-flex',
              borderRadius: 4,
              overflow: 'hidden',
              border: '1px solid var(--color-fill-3)',
            }}
          >
            {TIME_RANGES.map((range) => (
              <button
                key={range.value}
                onClick={() => handleRangeClick(range)}
                style={{
                  padding: '3px 10px',
                  fontSize: 12,
                  border: 'none',
                  cursor: 'pointer',
                  fontWeight: selectedRange === range.value ? 600 : 400,
                  background:
                    selectedRange === range.value
                      ? 'rgb(var(--primary-6))'
                      : 'var(--color-bg-1)',
                  color:
                    selectedRange === range.value
                      ? '#fff'
                      : 'var(--color-text-2)',
                  transition: 'all 0.15s',
                  borderRight: '1px solid var(--color-fill-3)',
                }}
              >
                {range.label}
              </button>
            ))}
            <DatePicker.RangePicker
              triggerElement={
                <button
                  style={{
                    padding: '3px 8px',
                    fontSize: 12,
                    border: 'none',
                    cursor: 'pointer',
                    background:
                      selectedRange === 'custom'
                        ? 'rgb(var(--primary-6))'
                        : 'var(--color-bg-1)',
                    color:
                      selectedRange === 'custom'
                        ? '#fff'
                        : 'var(--color-text-2)',
                    display: 'flex',
                    alignItems: 'center',
                  }}
                >
                  <IconCalendar />
                </button>
              }
              popupVisible={showDatePicker}
              onVisibleChange={(visible) => setShowDatePicker(!!visible)}
              showTime
              onChange={(dateStrings) => {
                if (dateStrings && dateStrings[0] && dateStrings[1]) {
                  onCustomRange(
                    new Date(String(dateStrings[0])).toISOString(),
                    new Date(String(dateStrings[1])).toISOString(),
                  )
                }
              }}
              style={{ width: 0, visibility: 'hidden', position: 'absolute' }}
            />
          </div>
        </>
      )}

      {/* More menu */}
      <Dropdown droplist={moreMenu} position="br">
        <Button size="small" type="secondary" icon={<IconMore />} />
      </Dropdown>
    </Space>
  )
}
