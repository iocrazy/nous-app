/**
 * Issues page — top-level user-visible "thing" entity (PR-D6).
 *
 * Layout: filter rail + list pane on the left, detail pane on the right.
 * Routes:
 *   /team/:teamId/issues               — list, no selection
 *   /team/:teamId/issues/:identifier   — list + detail for MH-N
 *
 * Live progress for dispatched issues comes from useDbosWorkflowStatus
 * (D4 SSE) keyed on snapshot.dbos_workflow_id.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import {
  Issue,
  IssueStatus,
  PRIORITY_LABEL,
  PRIORITY_ORDER,
  STATUS_LABEL,
  STATUS_ORDER,
  createIssue,
  deleteIssue,
  dispatchIssue,
  getIssueByIdentifier,
  listIssues,
  transitionIssueStatus,
} from '../services/issuesService';
import { useDbosWorkflowStatus } from '../hooks/useDbosWorkflowStatus';

// Tinted status chips — low-alpha semantic hues that read on both themes.
// Hue meaning matches the canonical text-only scheme in
// components/Todolist/IssueStatusIcon.tsx (todo=blue, in_progress=amber,
// in_review=purple, blocked=rose, done=emerald, backlog/cancelled=neutral).
const STATUS_COLOR: Record<IssueStatus, string> = {
  backlog: 'bg-ink-700/60 text-ink-300',
  todo: 'bg-blue-500/15 text-blue-300 border border-blue-500/30',
  in_progress: 'bg-amber-500/15 text-amber-300 border border-amber-500/30',
  in_review: 'bg-purple-500/15 text-purple-300 border border-purple-500/30',
  blocked: 'bg-rose-500/15 text-rose-300 border border-rose-500/30',
  done: 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30',
  cancelled: 'bg-ink-700/60 text-ink-400',
};

const PRIORITY_COLOR = {
  critical: 'text-rose-400',
  high: 'text-orange-400',
  medium: 'text-blue-400',
  low: 'text-ink-500',
};

export const IssuesPage: React.FC = () => {
  const navigate = useNavigate();
  const params = useParams();
  const teamId = params.teamId as string | undefined;
  const identifier = params.identifier as string | undefined;
  const prefix = teamId ? `/team/${teamId}` : '';

  // ── List state ────────────────────────────────────────────────
  const [items, setItems] = useState<Issue[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<IssueStatus | 'all'>('all');
  const [showNewModal, setShowNewModal] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listIssues({
        status: statusFilter === 'all' ? undefined : statusFilter,
        limit: 50,
      });
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // ── Detail state ──────────────────────────────────────────────
  const [selected, setSelected] = useState<Issue | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    if (!identifier) {
      setSelected(null);
      return;
    }
    setDetailLoading(true);
    getIssueByIdentifier(identifier)
      .then((row) => setSelected(row))
      .catch((e) =>
        setError(e instanceof Error ? e.message : `Failed to load ${identifier}`)
      )
      .finally(() => setDetailLoading(false));
  }, [identifier]);

  const handleSelect = (row: Issue) =>
    navigate(`${prefix}/issues/${row.identifier}`);

  const handleNew = async (title: string, description: string) => {
    try {
      const created = await createIssue({ title, description: description || undefined });
      setShowNewModal(false);
      await refresh();
      navigate(`${prefix}/issues/${created.identifier}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleTransition = async (status: IssueStatus) => {
    if (!selected) return;
    try {
      const updated = await transitionIssueStatus(selected.id, status);
      setSelected(updated);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleDispatch = async () => {
    if (!selected) return;
    try {
      const updated = await dispatchIssue(selected.id);
      setSelected(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleDelete = async () => {
    if (!selected) return;
    if (!confirm(`Delete ${selected.identifier}?`)) return;
    try {
      await deleteIssue(selected.id);
      navigate(`${prefix}/issues`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="flex-1 h-full min-h-0 overflow-hidden flex">
      {/* List pane */}
      <div className="w-96 flex-shrink-0 border-r border-line flex flex-col">
        <div className="px-4 py-3 border-b border-line flex items-center justify-between">
          <h2 className="text-lg font-semibold">Issues</h2>
          <button
            className="text-sm px-3 py-1 rounded btn-tint-indigo"
            onClick={() => setShowNewModal(true)}
          >
            + New
          </button>
        </div>
        <div className="px-4 py-2 border-b border-line flex flex-wrap gap-1 text-xs">
          <FilterPill
            label="All"
            active={statusFilter === 'all'}
            onClick={() => setStatusFilter('all')}
          />
          {STATUS_ORDER.map((s) => (
            <FilterPill
              key={s}
              label={STATUS_LABEL[s]}
              active={statusFilter === s}
              onClick={() => setStatusFilter(s)}
            />
          ))}
        </div>
        <div className="flex-1 overflow-y-auto">
          {loading && <div className="p-4 text-sm text-ink-500">Loading…</div>}
          {!loading && items.length === 0 && (
            <div className="p-6 text-sm text-ink-500 text-center">
              No issues. Click <em>+ New</em> to create one.
            </div>
          )}
          {items.map((issue) => (
            <IssueListRow
              key={issue.id}
              issue={issue}
              selected={identifier === issue.identifier}
              onClick={() => handleSelect(issue)}
            />
          ))}
        </div>
        {total > items.length && (
          <div className="p-2 text-xs text-ink-500 text-center border-t border-line">
            Showing {items.length} of {total}
          </div>
        )}
      </div>

      {/* Detail pane */}
      <div className="flex-1 min-w-0 overflow-y-auto">
        {error && (
          <div className="m-4 p-3 rounded bg-rose-500/15 text-rose-300 text-sm">
            {error}
          </div>
        )}
        {!selected && !detailLoading && (
          <div className="h-full flex items-center justify-center text-ink-500 text-sm">
            Select an issue from the list, or click <em>+ New</em>.
          </div>
        )}
        {detailLoading && (
          <div className="p-6 text-sm text-ink-500">Loading…</div>
        )}
        {selected && (
          <IssueDetail
            issue={selected}
            onTransition={handleTransition}
            onDispatch={handleDispatch}
            onDelete={handleDelete}
          />
        )}
      </div>

      {showNewModal && (
        <NewIssueModal
          onClose={() => setShowNewModal(false)}
          onCreate={handleNew}
        />
      )}
    </div>
  );
};

// ── Sub-components ────────────────────────────────────────────────

const FilterPill: React.FC<{
  label: string;
  active: boolean;
  onClick: () => void;
}> = ({ label, active, onClick }) => (
  <button
    className={`px-2 py-1 rounded ${
      active
        ? 'btn-tint-indigo'
        : 'bg-ink-800 text-ink-200 hover:bg-ink-700'
    }`}
    onClick={onClick}
  >
    {label}
  </button>
);

const IssueListRow: React.FC<{
  issue: Issue;
  selected: boolean;
  onClick: () => void;
}> = ({ issue, selected, onClick }) => (
  <button
    className={`w-full text-left px-4 py-3 border-b border-line hover:bg-ink-800/60 ${
      selected ? 'bg-indigo-500/10' : ''
    }`}
    onClick={onClick}
  >
    <div className="flex items-center justify-between text-xs text-ink-500">
      <span className="font-mono">{issue.identifier}</span>
      <span
        className={`px-1.5 py-0.5 rounded ${STATUS_COLOR[issue.status]}`}
      >
        {STATUS_LABEL[issue.status]}
      </span>
    </div>
    <div className="mt-1 text-sm font-medium truncate">{issue.title}</div>
    <div className="mt-1 flex items-center gap-2 text-xs">
      <span className={PRIORITY_COLOR[issue.priority]}>
        {PRIORITY_LABEL[issue.priority]}
      </span>
      <span className="text-ink-500">·</span>
      <span className="text-ink-500">
        {new Date(issue.created_at).toLocaleDateString()}
      </span>
    </div>
  </button>
);

const IssueDetail: React.FC<{
  issue: Issue;
  onTransition: (s: IssueStatus) => void;
  onDispatch: () => void;
  onDelete: () => void;
}> = ({ issue, onTransition, onDispatch, onDelete }) => {
  const { snapshot, isLoading, isTerminal, error } = useDbosWorkflowStatus(
    issue.dbos_workflow_id,
    { includeSteps: true }
  );

  const steps = useMemo(() => snapshot?.steps ?? [], [snapshot]);

  return (
    <div className="p-6 max-w-3xl">
      <div className="flex items-center gap-3 text-sm text-ink-500">
        <span className="font-mono">{issue.identifier}</span>
        <span
          className={`px-2 py-0.5 rounded ${STATUS_COLOR[issue.status]}`}
        >
          {STATUS_LABEL[issue.status]}
        </span>
        <span className={PRIORITY_COLOR[issue.priority]}>
          {PRIORITY_LABEL[issue.priority]}
        </span>
      </div>
      <h1 className="mt-2 text-2xl font-semibold">{issue.title}</h1>
      {issue.description && (
        <p className="mt-3 text-sm text-ink-200 whitespace-pre-wrap">
          {issue.description}
        </p>
      )}

      {/* Status transition */}
      <div className="mt-6 flex items-center gap-2">
        <label className="text-xs text-ink-500">Status:</label>
        <select
          className="text-sm border border-line bg-ink-900 text-ink-100 rounded px-2 py-1"
          value={issue.status}
          onChange={(e) => onTransition(e.target.value as IssueStatus)}
        >
          {STATUS_ORDER.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
      </div>

      {/* DBOS dispatch + live status */}
      <div className="mt-6 border-t border-line pt-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-ink-200">
            DBOS Workflow
          </h3>
          {!issue.dbos_workflow_id && (
            <button
              className="text-sm px-3 py-1 rounded btn-tint-green"
              onClick={onDispatch}
            >
              Dispatch
            </button>
          )}
        </div>
        {issue.dbos_workflow_id ? (
          <div className="mt-3 text-sm">
            <div className="text-xs text-ink-500 font-mono break-all">
              {issue.dbos_workflow_id}
            </div>
            {isLoading && (
              <div className="mt-2 text-ink-500">Connecting…</div>
            )}
            {error && (
              <div className="mt-2 text-rose-400">
                Stream error: {error.kind}
              </div>
            )}
            {snapshot && (
              <div className="mt-2 space-y-1">
                <div>
                  <span className="text-ink-500">Status:</span>{' '}
                  <span className="font-medium">
                    {snapshot.status ?? '(unknown)'}
                  </span>
                  {isTerminal && (
                    <span className="ml-2 text-xs text-ink-500">
                      (final)
                    </span>
                  )}
                </div>
                {snapshot.error && (
                  <div className="text-rose-400 text-xs whitespace-pre-wrap">
                    {snapshot.error}
                  </div>
                )}
                {steps.length > 0 && (
                  <details className="mt-2">
                    <summary className="text-xs text-ink-500 cursor-pointer">
                      {steps.length} step{steps.length > 1 ? 's' : ''}
                    </summary>
                    <ol className="mt-2 ml-4 text-xs text-ink-300 list-decimal">
                      {steps.map((s, i) => (
                        <li key={`${s.function_id}-${i}`}>
                          <span className="font-mono">{s.function_name}</span>
                          {s.error && (
                            <span className="text-rose-400"> — {s.error}</span>
                          )}
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
              </div>
            )}
          </div>
        ) : (
          <p className="mt-2 text-sm text-ink-500">
            Not dispatched. Click <em>Dispatch</em> to start the
            <code className="ml-1 px-1 py-0.5 bg-ink-800 rounded text-xs">
              execute_issue
            </code>{' '}
            DBOS workflow.
          </p>
        )}
      </div>

      {/* Meta */}
      <div className="mt-6 border-t border-line pt-4 text-xs text-ink-500 space-y-1">
        <div>
          Created: {new Date(issue.created_at).toLocaleString()}
        </div>
        <div>
          Updated: {new Date(issue.updated_at).toLocaleString()}
        </div>
        <div>
          Origin: <span className="font-mono">{issue.origin_kind}</span>
        </div>
        {issue.parent_id && (
          <div>
            Parent: <span className="font-mono">{issue.parent_id}</span>
          </div>
        )}
      </div>

      <div className="mt-8 border-t border-line pt-4 flex justify-end">
        <button
          className="text-sm text-rose-400 hover:underline"
          onClick={onDelete}
        >
          Delete
        </button>
      </div>
    </div>
  );
};

const NewIssueModal: React.FC<{
  onClose: () => void;
  onCreate: (title: string, description: string) => void;
}> = ({ onClose, onCreate }) => {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-island border border-line rounded-lg shadow-lg w-96 p-6">
        <h3 className="text-lg font-semibold mb-3 text-ink-100">New issue</h3>
        <input
          className="w-full border border-line bg-ink-900 text-ink-100 rounded px-3 py-2 text-sm"
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          autoFocus
        />
        <textarea
          className="mt-2 w-full border border-line bg-ink-900 text-ink-100 rounded px-3 py-2 text-sm h-32"
          placeholder="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            className="text-sm px-3 py-1 rounded text-ink-300 hover:bg-ink-800"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="text-sm px-3 py-1 rounded btn-tint-indigo disabled:opacity-50"
            disabled={!title.trim()}
            onClick={() => onCreate(title.trim(), description.trim())}
          >
            Create
          </button>
        </div>
      </div>
    </div>
  );
};

export default IssuesPage;
