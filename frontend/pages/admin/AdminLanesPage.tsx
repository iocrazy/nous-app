/**
 * AdminLanesPage — per-process 4-Lane queue snapshot (A 路线 PR #161).
 *
 * Process-local: each backend replica returns its own state. Auto-refresh
 * every 3s while the page is mounted.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Activity, Loader2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { lanesService, type LaneStatus, type LanesSnapshotResponse } from '../../services/lanesService';

const REFRESH_INTERVAL_MS = 3000;

function lanColor(saturation_pct: number): string {
  if (saturation_pct >= 90) return 'bg-rose-500';
  if (saturation_pct >= 60) return 'bg-amber-500';
  if (saturation_pct >= 20) return 'bg-blue-500';
  return 'bg-emerald-500';
}

const LaneRow: React.FC<{ lane: LaneStatus }> = ({ lane }) => (
  <div className="border border-gray-200 dark:border-gray-700 rounded p-3 bg-white dark:bg-gray-800">
    <div className="flex items-baseline justify-between mb-2">
      <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 capitalize">{lane.name}</h3>
      <span className="text-xs text-gray-500">
        {lane.in_flight}/{lane.capacity} in-flight · {lane.queued} queued
      </span>
    </div>
    <div className="h-2 bg-gray-200 dark:bg-gray-700 rounded overflow-hidden">
      <div
        className={`h-full transition-all ${lanColor(lane.saturation_pct)}`}
        style={{ width: `${Math.min(100, Math.max(0, lane.saturation_pct))}%` }}
      />
    </div>
    <div className="mt-2 grid grid-cols-3 gap-2 text-[11px] text-gray-500 dark:text-gray-400">
      <span>Saturation: <strong>{lane.saturation_pct.toFixed(1)}%</strong></span>
      <span>Last wait: <strong>{lane.last_wait_ms.toFixed(0)} ms</strong></span>
      <span>Total acquired: <strong>{lane.total_acquired.toLocaleString()}</strong></span>
    </div>
  </div>
);

export const AdminLanesPage: React.FC = () => {
  const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState<LanesSnapshotResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastFetched, setLastFetched] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await lanesService.snapshot();
      setSnapshot(data);
      setError(null);
      setLastFetched(Date.now());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load lanes');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-4">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate(-1)}
            className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700"
            title="Back"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <Activity className="w-5 h-5" />
            Lane queue snapshot
          </h1>
        </div>
        {lastFetched && (
          <span className="text-[11px] text-gray-400">
            updated {new Date(lastFetched).toLocaleTimeString()}
          </span>
        )}
      </header>

      <p className="text-xs text-gray-500">
        Process-local view. For cluster-wide saturation, query each replica.
        Auto-refresh every {REFRESH_INTERVAL_MS / 1000}s.
      </p>

      {loading && !snapshot && (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading…
        </div>
      )}
      {error && (
        <div className="text-sm text-rose-600 bg-rose-50 dark:bg-rose-900/20 px-3 py-2 rounded">
          {error}
        </div>
      )}
      {snapshot && snapshot.lanes.length === 0 && (
        <div className="text-sm text-gray-500 italic">No lanes registered (queue idle).</div>
      )}
      {snapshot && snapshot.lanes.length > 0 && (
        <div className="space-y-2">
          {snapshot.lanes.map((l) => <LaneRow key={l.name} lane={l} />)}
        </div>
      )}
    </div>
  );
};

export default AdminLanesPage;
