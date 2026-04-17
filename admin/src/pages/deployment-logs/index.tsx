import { useState, useEffect, useMemo } from 'react'
import {
  Card,
  Input,
  Tag,
  Typography,
  Empty,
  Spin,
  Radio,
} from '@arco-design/web-react'
import {
  IconSearch,
  IconClockCircle,
  IconUser,
  IconCode,
  IconDown,
  IconRight,
  IconBulb,
} from '@arco-design/web-react/icon'
import { supabase } from '../../auth/supabase'

const { Title, Text } = Typography

interface CommitEntry {
  sha: string
  subject: string
  author: string
  date: string
  type: string
}

interface DeploymentLog {
  id: string
  service: string
  version: string | null
  commit_sha: string | null
  commit_count: number
  commits: CommitEntry[]
  summary: string | null
  release_notes: string | null
  deployed_at: string
  deployed_by: string | null
  published_by: string | null
  status: string
}

const TYPE_TAG_COLOR: Record<string, string> = {
  feat: 'arcoblue',
  fix: 'orangered',
  perf: 'green',
  refactor: 'purple',
  docs: 'cyan',
  chore: 'gray',
  other: 'gray',
}

const SERVICE_COLOR: Record<string, string> = {
  backend: 'purple',
  frontend: 'cyan',
  extension: 'gold',
}

const DOT_STYLE: Record<string, React.CSSProperties> = {
  feat: { borderColor: '#6366f1', background: '#312e81' },
  fix: { borderColor: '#f59e0b', background: '#451a03' },
  perf: { borderColor: '#34d399', background: '#064e3b' },
  refactor: { borderColor: '#a78bfa', background: '#2e1065' },
  other: { borderColor: '#71717a', background: '#27272a' },
}

function formatTimeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    + ' · ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

function dominantType(commits: CommitEntry[]): string {
  if (!commits.length) return 'other'
  const counts: Record<string, number> = {}
  for (const c of commits) counts[c.type] = (counts[c.type] || 0) + 1
  return Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0]
}

/** Render release_notes with basic markdown-ish formatting */
function ReleaseNotes({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <div style={{ lineHeight: 1.7 }}>
      {lines.map((line, i) => {
        const trimmed = line.trim()
        if (!trimmed) return <div key={i} style={{ height: 8 }} />

        if (trimmed.startsWith('🎯')) {
          return (
            <div key={i} style={{
              background: 'rgba(99,102,241,0.1)',
              borderLeft: '3px solid #818cf8',
              padding: '8px 12px',
              borderRadius: '0 6px 6px 0',
              marginBottom: 12,
            }}>
              <span style={{ fontSize: 13 }}
                dangerouslySetInnerHTML={{
                  __html: trimmed.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                }}
              />
            </div>
          )
        }
        if (trimmed.startsWith('⚠️')) {
          return (
            <div key={i} style={{
              background: 'rgba(245,158,11,0.1)',
              border: '1px solid rgba(245,158,11,0.3)',
              borderRadius: 6,
              padding: '6px 10px',
              marginTop: 8,
            }}>
              <span style={{ fontSize: 12, color: '#f59e0b' }}
                dangerouslySetInnerHTML={{
                  __html: trimmed.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                }}
              />
            </div>
          )
        }
        if (trimmed.startsWith('### ') || trimmed.startsWith('## ')) {
          const label = trimmed.replace(/^#+\s*/, '')
          const typeMatch = label.match(/^(feat|fix|perf|refactor|docs|chore)/i)
          const tagType = typeMatch ? typeMatch[1].toLowerCase() : null
          return (
            <div key={i} style={{ marginTop: 12, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
              {tagType && <Tag size="small" color={TYPE_TAG_COLOR[tagType]}>{tagType}</Tag>}
              <Text bold style={{ fontSize: 13 }}>{label}</Text>
            </div>
          )
        }
        if (trimmed.startsWith('- ') || trimmed.startsWith('• ')) {
          return (
            <div key={i} style={{ paddingLeft: 16, fontSize: 13, position: 'relative' }}>
              <span style={{ position: 'absolute', left: 4, color: '#999' }}>•</span>
              <span dangerouslySetInnerHTML={{
                __html: trimmed.slice(2)
                  .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                  .replace(/`([^`]+)`/g, '<code style="background:#f0f0f0;padding:1px 4px;border-radius:3px;font-size:12px">$1</code>')
              }} />
            </div>
          )
        }
        return (
          <div key={i} style={{ fontSize: 13 }}>
            <span dangerouslySetInnerHTML={{
              __html: trimmed
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/`([^`]+)`/g, '<code style="background:#f0f0f0;padding:1px 4px;border-radius:3px;font-size:12px">$1</code>')
            }} />
          </div>
        )
      })}
    </div>
  )
}

export function DeploymentLogsPage() {
  const [logs, setLogs] = useState<DeploymentLog[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [showCommits, setShowCommits] = useState<Set<string>>(new Set())
  const [serviceFilter, setServiceFilter] = useState('all')
  const [searchQuery, setSearchQuery] = useState('')

  useEffect(() => {
    async function load() {
      setLoading(true)
      let query = supabase
        .from('deployment_logs')
        .select('*')
        .order('deployed_at', { ascending: false })
        .limit(100)
      if (serviceFilter !== 'all') {
        query = query.eq('service', serviceFilter)
      }
      const { data } = await query
      setLogs((data as DeploymentLog[]) || [])
      setLoading(false)
    }
    load()
  }, [serviceFilter])

  const filteredLogs = useMemo(() => {
    if (!searchQuery.trim()) return logs
    const q = searchQuery.toLowerCase()
    return logs.filter((log) => {
      if (log.summary?.toLowerCase().includes(q)) return true
      if (log.release_notes?.toLowerCase().includes(q)) return true
      if (log.commit_sha?.toLowerCase().includes(q)) return true
      if (log.version?.toLowerCase().includes(q)) return true
      const commits = Array.isArray(log.commits) ? log.commits : []
      return commits.some(c => c.subject?.toLowerCase().includes(q) || c.sha?.includes(q))
    })
  }, [logs, searchQuery])

  const toggleExpand = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const toggleCommits = (id: string) => {
    setShowCommits(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <Title heading={4} style={{ margin: 0 }}>Deployment Logs</Title>
          <Text type="secondary" style={{ fontSize: 13 }}>Auto-recorded with release notes by Claude Code</Text>
        </div>
        <Radio.Group
          type="button"
          size="small"
          value={serviceFilter}
          onChange={setServiceFilter}
        >
          <Radio value="all">All</Radio>
          <Radio value="backend">Backend</Radio>
          <Radio value="frontend">Frontend</Radio>
        </Radio.Group>
      </div>

      <Input
        prefix={<IconSearch />}
        placeholder="Search commits, SHA, summary, release notes..."
        value={searchQuery}
        onChange={setSearchQuery}
        allowClear
        style={{ marginBottom: 24 }}
      />

      {loading && <Spin style={{ display: 'block', textAlign: 'center', padding: 40 }} />}

      {!loading && filteredLogs.length === 0 && (
        <Empty description={searchQuery ? `No results for "${searchQuery}"` : 'No deployments logged yet'} />
      )}

      {/* Vertical timeline */}
      <div style={{ position: 'relative', paddingLeft: 28 }}>
        {/* Vertical line */}
        <div style={{
          position: 'absolute',
          left: 9,
          top: 0,
          bottom: 0,
          width: 2,
          background: '#e5e6eb',
        }} />

        {filteredLogs.map((log, idx) => {
          const isOpen = expanded.has(log.id)
          const commitsOpen = showCommits.has(log.id)
          const commits = Array.isArray(log.commits) ? log.commits : []
          const isFirst = idx === 0
          const dtype = dominantType(commits)
          const dotStyle = isFirst
            ? { borderColor: '#00b42a', background: '#00b42a', boxShadow: '0 0 8px rgba(0,180,42,0.4)', width: 16, height: 16, left: -20 }
            : { ...(DOT_STYLE[dtype] || DOT_STYLE.other), width: 14, height: 14, left: -19 }

          return (
            <div key={log.id} style={{ position: 'relative', marginBottom: 16 }}>
              {/* Dot */}
              <div style={{
                position: 'absolute',
                top: 14,
                borderRadius: '50%',
                border: '2px solid',
                zIndex: 2,
                ...dotStyle,
              }} />

              {/* Date + LIVE */}
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
                <IconClockCircle style={{ fontSize: 12 }} />
                {formatDate(log.deployed_at)}
                {isFirst && <Tag size="small" color="green">LIVE</Tag>}
              </div>

              {/* Card */}
              <Card
                size="small"
                style={{ borderRadius: 8, cursor: 'pointer' }}
                bodyStyle={{ padding: 0 }}
              >
                {/* Header */}
                <div
                  onClick={() => toggleExpand(log.id)}
                  style={{
                    padding: '10px 14px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    cursor: 'pointer',
                  }}
                >
                  {isOpen ? <IconDown style={{ fontSize: 12, color: '#86909c' }} /> : <IconRight style={{ fontSize: 12, color: '#86909c' }} />}
                  <Tag size="small" color={SERVICE_COLOR[log.service] || 'gray'}>{log.service}</Tag>
                  {log.commit_sha && (
                    <Text type="secondary" style={{ fontSize: 12, fontFamily: 'monospace' }}>
                      <IconCode style={{ fontSize: 11, marginRight: 2 }} />
                      {log.commit_sha.slice(0, 8)}
                    </Text>
                  )}
                  <Tag size="small" style={{ background: '#f2f3f5', color: '#86909c', border: 'none' }}>
                    {log.commit_count} commits
                  </Tag>
                  <div style={{ flex: 1, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {log.summary}
                  </div>
                  <Text type="secondary" style={{ fontSize: 11, flexShrink: 0 }}>
                    {formatTimeAgo(log.deployed_at)}
                  </Text>
                </div>

                {/* Expanded content */}
                {isOpen && (
                  <>
                    <div style={{ borderTop: '1px solid #f0f0f0', padding: '14px 18px' }}>
                      {log.release_notes ? (
                        <ReleaseNotes text={log.release_notes} />
                      ) : commits.length > 0 ? (
                        <div>
                          {commits.map((c) => (
                            <div key={c.sha} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, padding: '3px 0', fontSize: 13 }}>
                              <Tag size="small" color={TYPE_TAG_COLOR[c.type] || 'gray'}>{c.type}</Tag>
                              <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 11, flexShrink: 0 }}>{c.sha}</Text>
                              <span style={{ flex: 1 }}>{c.subject}</span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <Text type="secondary">No details recorded</Text>
                      )}

                      {(log.published_by || log.deployed_by) && (
                        <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid #f7f8fa', fontSize: 12, color: '#c0c0c0', display: 'flex', alignItems: 'center', gap: 4 }}>
                          <IconUser style={{ fontSize: 11 }} />
                          {log.published_by === 'claude-code'
                            ? <><span>Release notes by Claude Code</span><IconBulb style={{ color: '#6366f1', fontSize: 12 }} /></>
                            : <span>Deployed by {log.deployed_by}</span>
                          }
                        </div>
                      )}
                    </div>

                    {/* Raw commits toggle */}
                    {log.release_notes && commits.length > 0 && (
                      <>
                        <div
                          onClick={(e) => { e.stopPropagation(); toggleCommits(log.id) }}
                          style={{
                            padding: '6px 18px',
                            borderTop: '1px solid #f7f8fa',
                            fontSize: 12,
                            color: '#c0c0c0',
                            cursor: 'pointer',
                          }}
                        >
                          {commitsOpen ? '▾' : '▸'} Show {commits.length} raw commits
                        </div>
                        {commitsOpen && (
                          <div style={{ padding: '4px 18px 10px', background: '#fafafa' }}>
                            {commits.map((c) => (
                              <div key={c.sha} style={{ display: 'flex', gap: 6, fontSize: 12, padding: '2px 0', color: '#86909c' }}>
                                <span style={{ fontFamily: 'monospace', flexShrink: 0 }}>{c.sha}</span>
                                <span style={{ flex: 1 }}>{c.subject}</span>
                                <span style={{ flexShrink: 0, color: '#c0c0c0' }}>{c.author}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </>
                )}
              </Card>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default DeploymentLogsPage
