import React, { useEffect, useState, useMemo } from 'react';
import { getSupabaseClient } from '../../supabaseClient';
import { GitCommit, Clock, User, ChevronDown, ChevronRight, Search, X, Zap } from 'lucide-react';

interface CommitEntry {
  sha: string;
  subject: string;
  author: string;
  date: string;
  type: string;
}

interface DeploymentLog {
  id: string;
  service: string;
  version: string | null;
  commit_sha: string | null;
  commit_count: number;
  commits: CommitEntry[];
  summary: string | null;
  release_notes: string | null;
  deployed_at: string;
  deployed_by: string | null;
  published_by: string | null;
  status: string;
  metadata: Record<string, unknown>;
}

const TYPE_COLORS: Record<string, string> = {
  feat: 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30',
  fix: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
  perf: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  refactor: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  docs: 'bg-sky-500/20 text-sky-300 border-sky-500/30',
  chore: 'bg-zinc-500/20 text-zinc-400 border-zinc-500/30',
  other: 'bg-zinc-700 text-zinc-300 border-zinc-600',
};

const DOT_COLORS: Record<string, string> = {
  feat: 'border-indigo-400 bg-indigo-900',
  fix: 'border-amber-400 bg-amber-900',
  perf: 'border-emerald-400 bg-emerald-900',
  refactor: 'border-purple-400 bg-purple-900',
  other: 'border-zinc-500 bg-zinc-800',
};

function formatTimeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    + ' · ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

/** Determine dominant commit type for dot color */
function dominantType(commits: CommitEntry[]): string {
  if (!commits.length) return 'other';
  const counts: Record<string, number> = {};
  for (const c of commits) {
    counts[c.type] = (counts[c.type] || 0) + 1;
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0];
}

/** Escape HTML to prevent XSS before applying markdown replacements. */
function escapeHtml(input: string): string {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Apply minimal markdown (bold/code) on an already-escaped string. */
function renderInline(escaped: string): string {
  return escaped
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="text-indigo-300 bg-zinc-800 px-1 rounded text-[11px]">$1</code>');
}

/** Simple markdown-ish renderer for release_notes */
function ReleaseNotes({ text }: { text: string }) {
  const lines = text.split('\n');
  return (
    <div className="space-y-1">
      {lines.map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={i} className="h-2" />;
        // Highlight block (starts with 🎯)
        if (trimmed.startsWith('🎯')) {
          return (
            <div key={i} className="bg-indigo-950/60 border-l-3 border-indigo-500 pl-3 py-2 rounded-r-lg mb-2">
              <p className="text-[13px] text-indigo-200 leading-relaxed" dangerouslySetInnerHTML={{
                __html: renderInline(escapeHtml(trimmed))
              }} />
            </div>
          );
        }
        // Warning block
        if (trimmed.startsWith('⚠️')) {
          return (
            <div key={i} className="bg-amber-950/40 border border-amber-800/50 rounded-lg px-3 py-2 mt-2">
              <p className="text-[12px] text-amber-300" dangerouslySetInnerHTML={{
                __html: renderInline(escapeHtml(trimmed))
              }} />
            </div>
          );
        }
        // Section header (### or ## or bold line)
        if (trimmed.startsWith('### ') || trimmed.startsWith('## ')) {
          const label = trimmed.replace(/^#+\s*/, '');
          const typeMatch = label.match(/^(feat|fix|perf|refactor|docs|chore)/i);
          const tagType = typeMatch ? typeMatch[1].toLowerCase() : null;
          return (
            <div key={i} className="flex items-center gap-2 mt-3 mb-1">
              {tagType && (
                <span className={`px-1.5 py-0.5 text-[9px] font-semibold rounded border uppercase ${TYPE_COLORS[tagType] || TYPE_COLORS.other}`}>
                  {tagType}
                </span>
              )}
              <span className="text-[12px] font-semibold text-zinc-300">{label}</span>
            </div>
          );
        }
        // Bullet point
        if (trimmed.startsWith('- ') || trimmed.startsWith('• ')) {
          return (
            <div key={i} className="text-[12px] text-zinc-400 pl-4 relative leading-relaxed">
              <span className="absolute left-1 text-zinc-600">•</span>
              <span dangerouslySetInnerHTML={{
                __html: renderInline(escapeHtml(trimmed.slice(2)))
              }} />
            </div>
          );
        }
        // Regular text
        return (
          <p key={i} className="text-[12px] text-zinc-400 leading-relaxed" dangerouslySetInnerHTML={{
            __html: renderInline(escapeHtml(trimmed))
          }} />
        );
      })}
    </div>
  );
}

export const AdminDeploymentLogsPage: React.FC = () => {
  const [logs, setLogs] = useState<DeploymentLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [serviceFilter, setServiceFilter] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [showCommits, setShowCommits] = useState<Set<string>>(new Set());

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const supabase = getSupabaseClient();
        if (!supabase) throw new Error('Supabase not configured');
        let query = supabase
          .from('deployment_logs')
          .select('*')
          .order('deployed_at', { ascending: false })
          .limit(100);
        if (serviceFilter !== 'all') {
          query = query.eq('service', serviceFilter);
        }
        const { data, error: err } = await query;
        if (err) throw err;
        setLogs((data as DeploymentLog[]) || []);
      } catch (e: any) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [serviceFilter]);

  const filteredLogs = useMemo(() => {
    if (!searchQuery.trim()) return logs;
    const q = searchQuery.toLowerCase();
    return logs.filter((log) => {
      if (log.summary?.toLowerCase().includes(q)) return true;
      if (log.release_notes?.toLowerCase().includes(q)) return true;
      if (log.commit_sha?.toLowerCase().includes(q)) return true;
      if (log.version?.toLowerCase().includes(q)) return true;
      if (log.deployed_by?.toLowerCase().includes(q)) return true;
      const commits = Array.isArray(log.commits) ? log.commits : [];
      return commits.some(c => c.subject?.toLowerCase().includes(q) || c.sha?.includes(q));
    });
  }, [logs, searchQuery]);

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleCommits = (id: string) => {
    setShowCommits((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Deployment Logs</h1>
          <p className="text-xs text-zinc-500 mt-1">Auto-recorded with release notes by Claude Code</p>
        </div>
        <div className="flex gap-1.5">
          {['all', 'backend', 'frontend', 'extension'].map((s) => (
            <button
              key={s}
              onClick={() => setServiceFilter(s)}
              className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
                serviceFilter === s
                  ? 'bg-indigo-600 text-white'
                  : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Search */}
      <div className="relative mb-6">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
        <input
          type="text"
          placeholder="Search commits, SHA, summary, release notes..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full bg-zinc-900 border border-zinc-800 rounded-lg pl-9 pr-8 py-2 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-zinc-600 transition-colors"
        />
        {searchQuery && (
          <button
            onClick={() => setSearchQuery('')}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300"
          >
            <X size={14} />
          </button>
        )}
      </div>

      {loading && <div className="text-zinc-500 text-sm">Loading…</div>}
      {error && (
        <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {!loading && !error && filteredLogs.length === 0 && (
        <div className="text-center py-12 text-zinc-500 text-sm">
          {searchQuery ? `No results for "${searchQuery}"` : 'No deployments logged yet. Future releases will appear here.'}
        </div>
      )}

      {/* Vertical Timeline */}
      <div className="relative pl-8">
        {/* Vertical line */}
        <div className="absolute left-[11px] top-0 bottom-0 w-0.5 bg-zinc-800" />

        {filteredLogs.map((log, idx) => {
          const isOpen = expanded.has(log.id);
          const commitsOpen = showCommits.has(log.id);
          const commits = Array.isArray(log.commits) ? log.commits : [];
          const isFirst = idx === 0;
          const dtype = dominantType(commits);
          const dotClass = isFirst
            ? 'border-emerald-400 bg-emerald-500 shadow-[0_0_8px_rgba(52,211,153,0.4)] w-4 h-4 -left-[21px]'
            : `${DOT_COLORS[dtype] || DOT_COLORS.other} w-3.5 h-3.5 -left-5`;

          return (
            <div key={log.id} className="relative mb-4">
              {/* Dot */}
              <div className={`absolute top-4 rounded-full border-2 z-10 ${dotClass}`} />

              {/* Date + LIVE badge */}
              <div className="text-[11px] text-zinc-500 mb-1.5 flex items-center gap-2">
                {formatDate(log.deployed_at)}
                {isFirst && (
                  <span className="px-1.5 py-0.5 text-[9px] font-bold bg-emerald-900 text-emerald-300 border border-emerald-700 rounded uppercase tracking-wide">
                    Live
                  </span>
                )}
              </div>

              {/* Card */}
              <div className={`bg-zinc-900/60 border rounded-xl overflow-hidden transition-colors ${
                isOpen ? 'border-zinc-700' : 'border-zinc-800/60'
              }`}>
                {/* Header row */}
                <button
                  onClick={() => toggleExpand(log.id)}
                  className="w-full px-4 py-3 flex items-center gap-2.5 hover:bg-zinc-800/30 transition-colors text-left"
                >
                  <span className="text-zinc-500 shrink-0">
                    {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </span>
                  <span className={`px-1.5 py-0.5 text-[9px] font-semibold rounded uppercase tracking-wide ${
                    log.service === 'backend'
                      ? 'bg-violet-500/20 text-violet-300'
                      : log.service === 'frontend'
                      ? 'bg-cyan-500/20 text-cyan-300'
                      : 'bg-zinc-700 text-zinc-300'
                  }`}>
                    {log.service}
                  </span>
                  {log.commit_sha && (
                    <span className="text-[11px] text-zinc-500 font-mono flex items-center gap-1">
                      <GitCommit size={11} />
                      {log.commit_sha.slice(0, 8)}
                    </span>
                  )}
                  <span className="text-[10px] text-zinc-600 bg-zinc-800/80 px-1.5 py-0.5 rounded-full">
                    {log.commit_count} commit{log.commit_count !== 1 ? 's' : ''}
                  </span>
                  <div className="flex-1 text-[13px] text-zinc-300 truncate">{log.summary}</div>
                  <span className="text-[11px] text-zinc-600 shrink-0 flex items-center gap-1">
                    <Clock size={10} />
                    {formatTimeAgo(log.deployed_at)}
                  </span>
                </button>

                {/* Expanded: release notes + commits */}
                {isOpen && (
                  <>
                    <div className="border-t border-zinc-800 px-5 py-4 bg-zinc-950/50">
                      {log.release_notes ? (
                        <ReleaseNotes text={log.release_notes} />
                      ) : commits.length > 0 ? (
                        <ul className="space-y-1">
                          {commits.map((c) => (
                            <li key={c.sha} className="flex items-start gap-2 text-[12px]">
                              <span className={`px-1 py-0.5 text-[9px] font-semibold rounded border shrink-0 uppercase ${
                                TYPE_COLORS[c.type] || TYPE_COLORS.other
                              }`}>
                                {c.type}
                              </span>
                              <span className="font-mono text-[11px] text-zinc-600 shrink-0">{c.sha}</span>
                              <span className="text-zinc-300 flex-1">{c.subject}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-sm text-zinc-500">No details recorded</p>
                      )}

                      {/* Published by */}
                      {(log.published_by || log.deployed_by) && (
                        <div className="flex items-center gap-1.5 mt-3 pt-2 border-t border-zinc-800/50 text-[11px] text-zinc-600">
                          <User size={10} />
                          <span>{log.published_by === 'claude-code' ? 'Release notes by Claude Code' : `Deployed by ${log.deployed_by}`}</span>
                          {log.published_by === 'claude-code' && <Zap size={10} className="text-indigo-400" />}
                        </div>
                      )}
                    </div>

                    {/* Raw commits toggle */}
                    {log.release_notes && commits.length > 0 && (
                      <>
                        <button
                          onClick={(e) => { e.stopPropagation(); toggleCommits(log.id); }}
                          className="w-full px-5 py-2 border-t border-zinc-800/50 bg-zinc-950/30 text-[11px] text-zinc-600 hover:text-zinc-400 text-left transition-colors"
                        >
                          {commitsOpen ? '▾' : '▸'} Show {commits.length} raw commits
                        </button>
                        {commitsOpen && (
                          <div className="px-5 pb-3 bg-zinc-950/30 space-y-1">
                            {commits.map((c) => (
                              <div key={c.sha} className="flex items-start gap-2 text-[11px]">
                                <span className="font-mono text-zinc-600 shrink-0">{c.sha}</span>
                                <span className="text-zinc-500 flex-1">{c.subject}</span>
                                <span className="text-zinc-700 shrink-0">{c.author}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default AdminDeploymentLogsPage;
