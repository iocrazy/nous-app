import { useState, useCallback, useEffect } from 'react'
import {
  Card,
  Input,
  Tag,
  Space,
  Table,
  Typography,
  Checkbox,
  Button,
  Drawer,
  Timeline,
  Badge,
  Pagination,
  Tooltip,
  Message,
  Dropdown,
  Menu,
} from '@arco-design/web-react'
import {
  IconSearch,
  IconLink,
  IconSave,
  IconDelete,
  IconDown,
  IconExport,
} from '@arco-design/web-react/icon'
import { PageHeader } from '../../components/PageHeader'
import { EmptyState } from '../../components/EmptyState'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { useSearchParams } from 'react-router-dom'
import { TimeRangeSelector } from '../../components/TimeRangeSelector'
import {
  useSearchLogs,
  useRequestTrace,
  type UnifiedLogEntry,
} from '../../api/endpoints/search'
import { parseQuery, queryToParams } from '../../utils/query-parser'
import { exportToCsv } from '../../utils/csv-export'

const SOURCE_COLORS: Record<string, string> = {
  request: 'arcoblue',
  app: 'green',
  frontend: 'orangered',
  audit: 'purple',
}

const SOURCE_LABELS: Record<string, string> = {
  request: 'Request',
  app: 'App',
  frontend: 'Frontend',
  audit: 'Audit',
}

const LEVEL_COLORS: Record<string, string> = {
  INFO: 'blue',
  SUCCESS: 'green',
  WARNING: 'gold',
  ERROR: 'orangered',
  CRITICAL: 'magenta',
  DEBUG: 'gray',
}

const SAVED_SEARCHES_KEY = 'admin-saved-searches'

interface SavedSearch {
  name: string
  query: string
}

function getSavedSearches(): SavedSearch[] {
  try {
    return JSON.parse(localStorage.getItem(SAVED_SEARCHES_KEY) || '[]')
  } catch {
    return []
  }
}

function formatDateTime(dateStr: string): string {
  if (!dateStr) return '-'
  const d = new Date(dateStr)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const PAGE_SIZE = 50

export function GlobalSearch() {
  const [searchParams, setSearchParams] = useSearchParams()

  const [queryText, setQueryText] = useState(searchParams.get('q') || '')
  const [period, setPeriod] = useState(searchParams.get('period') || '24h')
  const [dateRange, setDateRange] = useState<[string, string] | null>(null)
  const [page, setPage] = useState(1)
  const [selectedSources, setSelectedSources] = useState<string[]>(
    (searchParams.get('sources') || 'request,app,frontend,audit').split(','),
  )
  const [selectedLevels, setSelectedLevels] = useState<string[]>([])
  const [traceRequestId, setTraceRequestId] = useState<string | null>(null)
  const [savedSearches, setSavedSearches] = useState<SavedSearch[]>(getSavedSearches())

  // Build search params
  const parsed = parseQuery(queryText)
  const filterParams = queryToParams(parsed)

  const searchApiParams = {
    q: filterParams.q || undefined,
    filters: filterParams.filters || undefined,
    sources: selectedSources.join(','),
    period: dateRange ? undefined : period,
    start_date: dateRange?.[0],
    end_date: dateRange?.[1],
    page,
    page_size: PAGE_SIZE,
  }

  const { data, isLoading } = useSearchLogs(searchApiParams)
  const { data: traceData } = useRequestTrace(traceRequestId)

  // Sync URL state
  useEffect(() => {
    const params: Record<string, string> = {}
    if (queryText) params.q = queryText
    if (period) params.period = period
    if (selectedSources.length < 4)
      params.sources = selectedSources.join(',')
    setSearchParams(params, { replace: true })
  }, [queryText, period, selectedSources, setSearchParams])

  const handleSearch = useCallback(
    (value: string) => {
      setQueryText(value)
      setPage(1)
    },
    [],
  )

  const handleSaveSearch = useCallback(() => {
    if (!queryText.trim()) return
    const name = prompt('Save search as:')
    if (!name) return
    const updated = [...savedSearches, { name, query: queryText }]
    setSavedSearches(updated)
    localStorage.setItem(SAVED_SEARCHES_KEY, JSON.stringify(updated))
    Message.success('Search saved')
  }, [queryText, savedSearches])

  const handleDeleteSavedSearch = useCallback(
    (idx: number) => {
      const updated = savedSearches.filter((_, i) => i !== idx)
      setSavedSearches(updated)
      localStorage.setItem(SAVED_SEARCHES_KEY, JSON.stringify(updated))
    },
    [savedSearches],
  )

  const handleExport = useCallback(() => {
    if (!data?.items?.length) return
    exportToCsv(
      `search-results-${new Date().toISOString().slice(0, 10)}.csv`,
      data.items as unknown as Record<string, unknown>[],
    )
  }, [data])

  // Filter by selected levels (client-side)
  const items = (data?.items ?? []).filter(
    (item) =>
      selectedLevels.length === 0 ||
      selectedLevels.includes(item.level || ''),
  )
  const total = data?.total ?? 0
  const facets = data?.facets

  const columns: ColumnProps<UnifiedLogEntry>[] = [
    {
      title: 'Time',
      dataIndex: 'timestamp',
      width: 160,
      render: (_, r) => formatDateTime(r.timestamp),
    },
    {
      title: 'Source',
      dataIndex: 'source',
      width: 90,
      render: (_, r) => (
        <Tag color={SOURCE_COLORS[r.source]} size="small">
          {SOURCE_LABELS[r.source] || r.source}
        </Tag>
      ),
    },
    {
      title: 'Level',
      dataIndex: 'level',
      width: 80,
      render: (_, r) =>
        r.level ? (
          <Tag color={LEVEL_COLORS[r.level] || 'gray'} size="small">
            {r.level}
          </Tag>
        ) : r.status_code ? (
          <Tag
            color={r.status_code >= 400 ? 'red' : 'green'}
            size="small"
          >
            {r.status_code}
          </Tag>
        ) : (
          '-'
        ),
    },
    {
      title: 'Message',
      dataIndex: 'message',
      render: (_, r) => (
        <Space size={4}>
          {r.module && (
            <Tag size="small" color="arcoblue" style={{ fontSize: 11, flexShrink: 0 }}>
              {r.module.split('.').pop()}
            </Tag>
          )}
          <Typography.Text style={{ fontSize: 13 }} ellipsis>
            {r.message}
          </Typography.Text>
          {r.request_id && (
            <Tooltip content="View request trace">
              <Button
                type="text"
                size="mini"
                icon={<IconLink />}
                onClick={(e) => {
                  e.stopPropagation()
                  setTraceRequestId(r.request_id!)
                }}
              />
            </Tooltip>
          )}
        </Space>
      ),
    },
  ]

  const savedSearchMenu = (
    <Menu
      onClickMenuItem={(key) => {
        if (key.startsWith('load-')) {
          const idx = parseInt(key.slice(5))
          setQueryText(savedSearches[idx].query)
          setPage(1)
        }
      }}
    >
      {savedSearches.length === 0 ? (
        <Menu.Item key="empty" disabled>
          No saved searches
        </Menu.Item>
      ) : (
        savedSearches.map((s, i) => (
          <Menu.Item key={`load-${i}`}>
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <span>{s.name}</span>
              <Button
                type="text"
                size="mini"
                icon={<IconDelete />}
                onClick={(e) => {
                  e.stopPropagation()
                  handleDeleteSavedSearch(i)
                }}
              />
            </Space>
          </Menu.Item>
        ))
      )}
    </Menu>
  )

  return (
    <div>
      <PageHeader
        title="Global Search"
        subtitle="Search across all log sources with KQL-like query syntax"
        icon={<IconSearch />}
        breadcrumb={['Logs & Monitoring', 'Search']}
      />
      {/* Search Bar */}
      <Card style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size="medium">
          <Space style={{ width: '100%' }}>
            <Input.Search
              placeholder='Search logs... e.g. level:ERROR AND module:supabase*'
              value={queryText}
              onChange={setQueryText}
              onSearch={handleSearch}
              style={{ flex: 1 }}
              prefix={<IconSearch />}
            />
            <Dropdown droplist={savedSearchMenu} position="bl">
              <Button icon={<IconDown />}>Saved</Button>
            </Dropdown>
            <Button icon={<IconSave />} onClick={handleSaveSearch}>
              Save
            </Button>
            <Button icon={<IconExport />} onClick={handleExport}>
              Export
            </Button>
          </Space>
          <TimeRangeSelector
            period={period}
            onPeriodChange={setPeriod}
            dateRange={dateRange}
            onDateRangeChange={setDateRange}
          />
        </Space>
      </Card>

      <div style={{ display: 'flex', gap: 16 }}>
        {/* Facets Sidebar */}
        <Card style={{ width: 220, flexShrink: 0 }}>
          <Typography.Title heading={6} style={{ marginTop: 0, marginBottom: 12 }}>
            Source
          </Typography.Title>
          <Checkbox.Group
            value={selectedSources}
            onChange={(v) => {
              setSelectedSources(v as string[])
              setPage(1)
            }}
            direction="vertical"
          >
            {['request', 'app', 'frontend', 'audit'].map((s) => (
              <Checkbox key={s} value={s}>
                <Space>
                  <Tag color={SOURCE_COLORS[s]} size="small">
                    {SOURCE_LABELS[s]}
                  </Tag>
                  <Badge
                    count={facets?.sources?.[s] || 0}
                    dotStyle={{ background: '#86909c' }}
                  />
                </Space>
              </Checkbox>
            ))}
          </Checkbox.Group>

          <Typography.Title heading={6} style={{ marginTop: 16, marginBottom: 12 }}>
            Level
          </Typography.Title>
          <Checkbox.Group
            value={selectedLevels}
            onChange={(v) => {
              setSelectedLevels(v as string[])
              setPage(1)
            }}
            direction="vertical"
          >
            {Object.entries(facets?.levels || {})
              .sort((a, b) => b[1] - a[1])
              .map(([level, count]) => (
                <Checkbox key={level} value={level}>
                  <Space>
                    <Tag color={LEVEL_COLORS[level] || 'gray'} size="small">
                      {level}
                    </Tag>
                    <Badge
                      count={count}
                      dotStyle={{ background: '#86909c' }}
                    />
                  </Space>
                </Checkbox>
              ))}
          </Checkbox.Group>

          {Object.keys(facets?.modules || {}).length > 0 && (
            <>
              <Typography.Title
                heading={6}
                style={{ marginTop: 16, marginBottom: 12 }}
              >
                Module
              </Typography.Title>
              {Object.entries(facets?.modules || {})
                .sort((a, b) => b[1] - a[1])
                .slice(0, 10)
                .map(([mod, count]) => (
                  <div key={mod} style={{ marginBottom: 4 }}>
                    <Typography.Text
                      style={{ fontSize: 12, cursor: 'pointer' }}
                      onClick={() => {
                        setQueryText(`module:${mod}`)
                        setPage(1)
                      }}
                    >
                      {mod}{' '}
                      <Badge
                        count={count}
                        dotStyle={{ background: '#86909c' }}
                      />
                    </Typography.Text>
                  </div>
                ))}
            </>
          )}
        </Card>

        {/* Results */}
        <Card style={{ flex: 1 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              marginBottom: 12,
            }}
          >
            <Typography.Text>
              Results ({total})
              {selectedSources.map((s) => (
                <Tag
                  key={s}
                  color={SOURCE_COLORS[s]}
                  size="small"
                  style={{ marginLeft: 8 }}
                >
                  {SOURCE_LABELS[s]}: {facets?.sources?.[s] || 0}
                </Tag>
              ))}
            </Typography.Text>
          </div>

          <Table
            rowKey="id"
            columns={columns}
            data={items}
            loading={isLoading}
            pagination={false}
            size="small"
            noDataElement={<EmptyState description="No results found" />}
          />

          {total > PAGE_SIZE && (
            <div style={{ marginTop: 16, textAlign: 'right' }}>
              <Pagination
                current={page}
                pageSize={PAGE_SIZE}
                total={total}
                onChange={setPage}
                showTotal={(t) => `Total ${t} entries`}
                sizeCanChange={false}
              />
            </div>
          )}
        </Card>
      </div>

      {/* Request Trace Drawer */}
      <Drawer
        width={560}
        title={`Request Trace: ${traceRequestId || ''}`}
        visible={!!traceRequestId}
        onCancel={() => setTraceRequestId(null)}
        footer={null}
      >
        {traceData && (
          <>
            <Card style={{ marginBottom: 16 }}>
              <Space>
                <Tag color="arcoblue">{traceData.method}</Tag>
                <Typography.Text>{traceData.path}</Typography.Text>
                <Tag color={traceData.status_code && traceData.status_code >= 400 ? 'red' : 'green'}>
                  {traceData.status_code}
                </Tag>
                <Typography.Text type="secondary">
                  {traceData.total_ms}ms
                </Typography.Text>
              </Space>
            </Card>

            <Timeline>
              {traceData.entries.map((entry, i) => (
                <Timeline.Item
                  key={i}
                  label={`${entry.offset_ms}ms`}
                  dotColor={
                    entry.source === 'request'
                      ? '#3370ff'
                      : entry.level === 'ERROR' || entry.level === 'CRITICAL'
                        ? '#f53f3f'
                        : entry.level === 'WARNING'
                          ? '#ff7d00'
                          : '#00b42a'
                  }
                >
                  <Space direction="vertical" size={2}>
                    <Space size={4}>
                      {entry.level && (
                        <Tag
                          color={LEVEL_COLORS[entry.level] || 'gray'}
                          size="small"
                        >
                          {entry.level}
                        </Tag>
                      )}
                      {entry.module && (
                        <Tag size="small" color="arcoblue">
                          {entry.module}
                        </Tag>
                      )}
                    </Space>
                    <Typography.Text style={{ fontSize: 13 }}>
                      {entry.message}
                    </Typography.Text>
                  </Space>
                </Timeline.Item>
              ))}
            </Timeline>
          </>
        )}
      </Drawer>
    </div>
  )
}
