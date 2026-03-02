import { Button, DatePicker, Space } from '@arco-design/web-react'

const PERIODS = ['1h', '6h', '24h', '7d', '30d'] as const

const PERIOD_HOURS: Record<string, number> = {
  '1h': 1, '6h': 6, '24h': 24, '7d': 168, '30d': 720,
}

export function periodToDateRange(
  period: string,
  dateRange: [string, string] | null,
): { start_date?: string; end_date?: string } {
  if (dateRange) {
    return { start_date: dateRange[0], end_date: dateRange[1] }
  }
  const hours = PERIOD_HOURS[period]
  if (!hours) return {}
  const end = new Date()
  const start = new Date(end.getTime() - hours * 3600_000)
  return { start_date: start.toISOString(), end_date: end.toISOString() }
}

interface TimeRangeSelectorProps {
  period: string
  onPeriodChange: (period: string) => void
  dateRange: [string, string] | null
  onDateRangeChange: (range: [string, string] | null) => void
}

export function TimeRangeSelector({
  period,
  onPeriodChange,
  dateRange,
  onDateRangeChange,
}: TimeRangeSelectorProps) {
  return (
    <Space size="medium">
      {PERIODS.map((p) => (
        <Button
          key={p}
          type={period === p && !dateRange ? 'primary' : 'default'}
          size="small"
          onClick={() => {
            onPeriodChange(p)
            onDateRangeChange(null)
          }}
        >
          {p}
        </Button>
      ))}
      <DatePicker.RangePicker
        size="small"
        showTime
        style={{ width: 360 }}
        onChange={(dateStrings) => {
          if (dateStrings?.[0] && dateStrings?.[1]) {
            onDateRangeChange([dateStrings[0], dateStrings[1]])
            onPeriodChange('')
          } else {
            onDateRangeChange(null)
            onPeriodChange('24h')
          }
        }}
      />
    </Space>
  )
}
