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

const STATUS_COLOR: Record<IssueStatus, string> = {
  backlog: 'bg-gray-200 text-gray-800',
  todo: 'bg-blue-100 text-blue-800',
  in_progress: 'bg-amber-100 text-amber-800',
  in_review: 'bg-purple-100 text-purple-800',
  blocked: 'bg-red-100 text-red-800',
  done: 'bg-green-100 text-green-800',
  cancelled: 'bg-gray-100 text-gray-500',
};

const PRIORITY_COLOR = {
  critical: 'text-red-600',
  high: 'text-orange-600',
  medium: 'text-blue-600',
  low: 'text-gray-500',
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
      <div className="w-96 flex-shrink-0 border-r border-gray-200 flex flex-col">
        <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Issues</h2>
          <button
            className="text-sm px-3 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700"
            onClick={() => setShowNewModal(true)}
          >
            + New
          </button>
        </div>
        <div className="px-4 py-2 border-b border-gray-100 flex flex-wrap gap-1 text-xs">
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
          {loading && <div className="p-4 text-sm text-gray-500">Loading…</div>}
          {!loading && items.length === 0 && (
            <div className="p-6 text-sm text-gray-500 text-center">
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
          <div className="p-2 text-xs text-gray-400 text-center border-t">
            Showing {items.length} of {total}
          </div>
        )}
      </div>

      {/* Detail pane */}
      <div className="flex-1 min-w-0 overflow-y-auto">
        {error && (
          <div className="m-4 p-3 rounded bg-red-50 text-red-800 text-sm">
            {error}
          </div>
        )}
        {!selected && !detailLoading && (
          <div className="h-full flex items-center justify-center text-gray-400 text-sm">
            Select an issue from the list, or click <em>+ New</em>.
          </div>
        )}
        {detailLoading && (
          <div className="p-6 text-sm text-gray-500">Loading…</div>
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
        ? 'bg-indigo-600 text-white'
        : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
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
    className={`w-full text-left px-4 py-3 border-b border-gray-100 hover:bg-gray-50 ${
      selected ? 'bg-indigo-50' : ''
    }`}
    onClick={onClick}
  >
    <div className="flex items-center justify-between text-xs text-gray-500">
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
      <span className="text-gray-400">·</span>
      <span className="text-gray-500">
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
      <div className="flex items-center gap-3 text-sm text-gray-500">
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
        <p className="mt-3 text-sm text-gray-700 whitespace-pre-wrap">
          {issue.description}
        </p>
      )}

      {/* Status transition */}
      <div className="mt-6 flex items-center gap-2">
        <label className="text-xs text-gray-500">Status:</label>
        <select
          className="text-sm border border-gray-300 rounded px-2 py-1"
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
      <div className="mt-6 border-t border-gray-200 pt-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-700">
            DBOS Workflow
          </h3>
          {!issue.dbos_workflow_id && (
            <button
              className="text-sm px-3 py-1 rounded bg-emerald-600 text-white hover:bg-emerald-700"
              onClick={onDispatch}
            >
              Dispatch
            </button>
          )}
        </div>
        {issue.dbos_workflow_id ? (
          <div className="mt-3 text-sm">
            <div className="text-xs text-gray-500 font-mono break-all">
              {issue.dbos_workflow_id}
            </div>
            {isLoading && (
              <div className="mt-2 text-gray-500">Connecting…</div>
            )}
            {error && (
              <div className="mt-2 text-red-600">
                Stream error: {error.kind}
              </div>
            )}
            {snapshot && (
              <div className="mt-2 space-y-1">
                <div>
                  <span className="text-gray-500">Status:</span>{' '}
                  <span className="font-medium">
                    {snapshot.status ?? '(unknown)'}
                  </span>
                  {isTerminal && (
                    <span className="ml-2 text-xs text-gray-400">
                      (final)
                    </span>
                  )}
                </div>
                {snapshot.error && (
                  <div className="text-red-600 text-xs whitespace-pre-wrap">
                    {snapshot.error}
                  </div>
                )}
                {steps.length > 0 && (
                  <details className="mt-2">
                    <summary className="text-xs text-gray-500 cursor-pointer">
                      {steps.length} step{steps.length > 1 ? 's' : ''}
                    </summary>
                    <ol className="mt-2 ml-4 text-xs text-gray-600 list-decimal">
                      {steps.map((s, i) => (
                        <li key={`${s.function_id}-${i}`}>
                          <span className="font-mono">{s.function_name}</span>
                          {s.error && (
                            <span className="text-red-600"> — {s.error}</span>
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
          <p className="mt-2 text-sm text-gray-500">
            Not dispatched. Click <em>Dispatch</em> to start the
            <code className="ml-1 px-1 py-0.5 bg-gray-100 rounded text-xs">
              execute_issue
            </code>{' '}
            DBOS workflow.
          </p>
        )}
      </div>

      {/* Meta */}
      <div className="mt-6 border-t border-gray-200 pt-4 text-xs text-gray-500 space-y-1">
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

      <div className="mt-8 border-t border-gray-200 pt-4 flex justify-end">
        <button
          className="text-sm text-red-600 hover:underline"
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
    <div className="fixed inset-0 bg-black bg-opacity-30 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-lg w-96 p-6">
        <h3 className="text-lg font-semibold mb-3">New issue</h3>
        <input
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          autoFocus
        />
        <textarea
          className="mt-2 w-full border border-gray-300 rounded px-3 py-2 text-sm h-32"
          placeholder="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            className="text-sm px-3 py-1 rounded text-gray-600 hover:bg-gray-100"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="text-sm px-3 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-gray-300"
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
