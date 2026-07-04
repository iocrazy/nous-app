import { useMemo } from 'react'
import { Tag, Button, Typography } from '@arco-design/web-react'
import { IconExport } from '@arco-design/web-react/icon'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import { useAiUsageModels } from '../../api/endpoints/ai-usage'
import type { AiUsageRow } from '../../api/endpoints/ai-usage'
import { formatDateTime } from '../../utils/format'
import { exportToCsv } from '../../utils/csv-export'

// --- Constants ---

const STATUS_FILTER_OPTIONS = [
  { label: 'Completed', value: 'completed' },
  { label: 'Running', value: 'running' },
  { label: 'Failed', value: 'failed' },
  { label: 'Cancelled', value: 'cancelled' },
  { label: 'Heartbeat Lost', value: 'heartbeat_lost' },
]

const STATUS_COLORS: Record<string, string> = {
  completed: 'green',
  running: 'blue',
  failed: 'red',
  cancelled: 'gray',
  heartbeat_lost: 'orange',
}

const EXPORT_COLUMNS = [
  { key: 'started_at', label: 'Time' },
  { key: 'user_email', label: 'User' },
  { key: 'model', label: 'Model' },
  { key: 'provider', label: 'Provider' },
  { key: 'prompt_tokens', label: 'Prompt Tokens' },
  { key: 'completion_tokens', label: 'Completion Tokens' },
  { key: 'total_tokens', label: 'Total Tokens' },
  { key: 'cost_cents', label: 'Cost (cents)' },
  { key: 'status', label: 'Status' },
  { key: 'duration_ms', label: 'Duration (ms)' },
  { key: 'trigger', label: 'Trigger' },
  { key: 'error_code', label: 'Error' },
]

// --- Sub-components ---

function StatusTag({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? 'gray'
  const label = status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  return <Tag color={color}>{label}</Tag>
}

function formatCost(cents: number): string {
  return `$${(cents / 100).toFixed(4)}`
}

function formatDuration(ms: number | null): string {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

// --- Main component ---

export function AiUsagePage() {
  const { data: models } = useAiUsageModels()

  const columns = useMemo<NotionColumnDef<AiUsageRow>[]>(
    () => [
      {
        key: 'started_at',
        header: 'Time',
        type: 'date',
        sortable: true,
        required: true,
        size: 160,
        cell: (row) => (row.started_at ? formatDateTime(row.started_at) : '-'),
      },
      {
        key: 'user_email',
        header: 'User',
        type: 'text',
        size: 200,
        cell: (row) => (
          <Typography.Text ellipsis={{ showTooltip: true }}>
            {row.user_email || row.user_id || '-'}
          </Typography.Text>
        ),
      },
      {
        key: 'model',
        header: 'Model',
        type: 'select',
        filterable: true,
        size: 180,
        filterOptions: (models ?? []).map((m) => ({ label: m, value: m })),
        cell: (row) => row.model || '-',
      },
      {
        key: 'provider',
        header: 'Provider',
        type: 'text',
        size: 120,
        cell: (row) => row.provider || '-',
      },
      {
        key: 'total_tokens',
        header: 'Tokens',
        type: 'number',
        sortable: true,
        size: 110,
        cell: (row) => (
          <Typography.Text style={{ fontVariantNumeric: 'tabular-nums' }}>
            {row.total_tokens.toLocaleString()}
          </Typography.Text>
        ),
      },
      {
        key: 'cost_cents',
        header: 'Cost',
        type: 'number',
        sortable: true,
        size: 100,
        cell: (row) => formatCost(row.cost_cents),
      },
      {
        key: 'status',
        header: 'Status',
        type: 'select',
        filterable: true,
        size: 130,
        filterOptions: STATUS_FILTER_OPTIONS,
        cell: (row) => <StatusTag status={row.status} />,
      },
      {
        key: 'duration_ms',
        header: 'Duration',
        type: 'number',
        size: 100,
        cell: (row) => formatDuration(row.duration_ms),
      },
      {
        key: 'trigger',
        header: 'Trigger',
        type: 'text',
        size: 130,
        cell: (row) => row.trigger || '-',
      },
      {
        key: 'error_code',
        header: 'Error',
        type: 'text',
        size: 140,
        cell: (row) =>
          row.error_code ? (
            <Typography.Text type="error" ellipsis={{ showTooltip: true }}>
              {row.error_code}
            </Typography.Text>
          ) : (
            '-'
          ),
      },
    ],
    [models],
  )

  const { table, toolbarProps, pagination, isLoading, setPage, queryResult } =
    useNotionTable<AiUsageRow>({
      tableKey: 'ai-usage',
      columns,
      defaultSorts: [{ field: 'started_at', direction: 'desc' }],
      refetchInterval: 30000,
      fetchData: async ({ page, pageSize, filters, sorts }) => {
        const modelFilter = filters.find((f) => f.field === 'model')
        const statusFilter = filters.find((f) => f.field === 'status')
        const { data } = await apiClient.get('/api/v1/admin/ai-usage/runs', {
          params: {
            page,
            page_size: pageSize,
            ...(modelFilter?.value && { model: modelFilter.value }),
            ...(statusFilter?.value && { status: statusFilter.value }),
            ...(sorts[0]?.field && { sort_by: sorts[0].field }),
            ...(sorts[0]?.direction && { sort_order: sorts[0].direction }),
          },
        })
        return { items: data.items, total: data.total }
      },
    })

  const handleExport = () => {
    const items = queryResult.data?.items ?? []
    exportToCsv(
      'ai-usage.csv',
      items as unknown as Record<string, unknown>[],
      EXPORT_COLUMNS,
    )
  }

  return (
    <NotionTable<AiUsageRow>
      table={table}
      toolbarProps={toolbarProps}
      pagination={pagination}
      onPageChange={setPage}
      isLoading={isLoading}
      emptyText="No AI usage found"
      scrollX={1340}
      toolbarExtra={
        <Button icon={<IconExport />} size="small" onClick={handleExport}>
          Export
        </Button>
      }
    />
  )
}
