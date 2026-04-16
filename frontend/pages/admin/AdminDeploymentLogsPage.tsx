import React, { useEffect, useState } from 'react';
import { getSupabaseClient } from '../../supabaseClient';
import { GitCommit, Clock, User, ChevronDown, ChevronRight } from 'lucide-react';

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
  deployed_at: string;
  deployed_by: string | null;
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

export const AdminDeploymentLogsPage: React.FC = () => {
  const [logs, setLogs] = useState<DeploymentLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [serviceFilter, setServiceFilter] = useState<string>('all');

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

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-100">Deployment Logs</h1>
          <p className="text-sm text-zinc-500 mt-1">Auto-recorded from CI/CD after each release</p>
        </div>
        <div className="flex gap-2">
          {['all', 'backend', 'frontend', 'extension'].map((s) => (
            <button
              key={s}
              onClick={() => setServiceFilter(s)}
              className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                serviceFilter === s
                  ? 'bg-indigo-600 text-white'
                  : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="text-zinc-500">Loading…</div>}
      {error && (
        <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {!loading && !error && logs.length === 0 && (
        <div className="text-center py-12 text-zinc-500">
          No deployments logged yet. Future releases will appear here.
        </div>
      )}

      <div className="space-y-3">
        {logs.map((log) => {
          const isOpen = expanded.has(log.id);
          const commits = Array.isArray(log.commits) ? log.commits : [];
          return (
            <div
              key={log.id}
              className="bg-zinc-900/50 border border-zinc-800 rounded-xl overflow-hidden"
            >
              <button
                onClick={() => toggleExpand(log.id)}
                className="w-full px-4 py-3 flex items-center gap-3 hover:bg-zinc-800/40 transition-colors text-left"
              >
                {isOpen ? (
                  <ChevronDown size={16} className="text-zinc-500 shrink-0" />
                ) : (
                  <ChevronRight size={16} className="text-zinc-500 shrink-0" />
                )}
                <span
                  className={`px-2 py-0.5 text-[10px] font-medium rounded uppercase tracking-wide ${
                    log.service === 'backend'
                      ? 'bg-violet-500/20 text-violet-300'
                      : log.service === 'frontend'
                      ? 'bg-cyan-500/20 text-cyan-300'
                      : 'bg-zinc-700 text-zinc-300'
                  }`}
                >
                  {log.service}
                </span>
                {log.version && log.version !== 'latest' && (
                  <span className="text-xs text-zinc-400 font-mono">{log.version}</span>
                )}
                {log.commit_sha && (
                  <span className="text-xs text-zinc-500 font-mono flex items-center gap-1">
                    <GitCommit size={12} />
                    {log.commit_sha}
                  </span>
                )}
                <span className="text-xs text-zinc-600">
                  {log.commit_count} commit{log.commit_count !== 1 ? 's' : ''}
                </span>
                <div className="flex-1 text-sm text-zinc-300 truncate">{log.summary}</div>
                {log.deployed_by && (
                  <span className="text-xs text-zinc-500 flex items-center gap-1">
                    <User size={11} />
                    {log.deployed_by}
                  </span>
                )}
                <span className="text-xs text-zinc-500 flex items-center gap-1 shrink-0">
                  <Clock size={11} />
                  {formatTimeAgo(log.deployed_at)}
                </span>
              </button>

              {isOpen && (
                <div className="border-t border-zinc-800 bg-zinc-950/40 px-4 py-3">
                  {commits.length === 0 ? (
                    <div className="text-sm text-zinc-500">No commit details recorded</div>
                  ) : (
                    <ul className="space-y-1.5">
                      {commits.map((c) => (
                        <li key={c.sha} className="flex items-start gap-2 text-sm">
                          <span
                            className={`px-1.5 py-0.5 text-[10px] font-semibold rounded border ${
                              TYPE_COLORS[c.type] || TYPE_COLORS.other
                            } shrink-0 uppercase`}
                          >
                            {c.type}
                          </span>
                          <span className="font-mono text-xs text-zinc-500 shrink-0">
                            {c.sha}
                          </span>
                          <span className="text-zinc-300 flex-1">{c.subject}</span>
                          <span className="text-xs text-zinc-600 shrink-0">{c.author}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default AdminDeploymentLogsPage;
