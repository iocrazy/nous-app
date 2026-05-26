/**
 * Settings panel section: choose the TTL for chat attachment temp resources.
 *
 * The dropdown maps to days (positive ints) or `-1` (never expire). The
 * sweeper reads this value daily and soft-deletes expired temp resources.
 */

import { useEffect, useState } from 'react';
import { tempTtlService, ScopeType } from '../services/tempTtlService';

interface Props {
  scopeType: ScopeType;
  scopeId: string;
  /** Optional display label (e.g., team name or "Personal"). Falls back to scopeType. */
  label?: string;
}

const OPTIONS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 7, label: '7 days' },
  { value: 14, label: '14 days' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
  { value: -1, label: 'Never' },
];

export function ChatTempTtlPanel({ scopeType, scopeId, label }: Props) {
  const [ttl, setTtl] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setTtl(null);
    tempTtlService
      .getChatTempTtl(scopeType, scopeId)
      .then((r) => {
        if (!cancelled) setTtl(r.ttl_days);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [scopeType, scopeId]);

  async function onChange(next: number) {
    setSaving(true);
    setError(null);
    try {
      const r = await tempTtlService.setChatTempTtl(scopeType, scopeId, next);
      setTtl(r.ttl_days);
    } catch (err: unknown) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 py-3">
      <label
        htmlFor={`ttl-${scopeType}-${scopeId}`}
        className="text-sm font-medium text-gray-700 dark:text-gray-200"
      >
        {label ? `Chat attachment TTL — ${label}` : `Chat attachment TTL (${scopeType})`}
      </label>
      <select
        id={`ttl-${scopeType}-${scopeId}`}
        className="rounded border px-2 py-1 text-sm dark:bg-gray-800 dark:border-gray-600"
        value={ttl === null ? '' : String(ttl)}
        disabled={ttl === null || saving}
        onChange={(e) => void onChange(Number(e.target.value))}
      >
        {ttl === null && <option value="">Loading…</option>}
        {OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {error && (
        <span className="text-xs text-red-600 dark:text-red-400">{error}</span>
      )}
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Temp uploads in this scope are soft-deleted after this many days.
        Promoted resources (Saved out of the temp folder) are not affected.
      </p>
    </div>
  );
}
