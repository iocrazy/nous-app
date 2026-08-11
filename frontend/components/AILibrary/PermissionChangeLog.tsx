// frontend/components/AILibrary/PermissionChangeLog.tsx
//
// Read-only history of chat_permissions/capabilities changes for one agent
// (2026-08-10 spec §3). Rendered inside AgentEditor's Permissions tab, below
// PermissionsSection — AgentEditor owns fetching, lazy-loading on first tab
// activation and re-fetching after a save; this component only formats what
// it's given.
//
// 403 (non-owner) is handled by the caller by not mounting this component at
// all — silent, no error UI. `loading=true` and `audits=[]` are the only two
// states this component itself distinguishes.

import { useTranslation } from 'react-i18next';
import type { AgentPermissionAudit } from '../../types';

interface Props {
  audits: AgentPermissionAudit[];
  loading: boolean;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Em dash for "not present on this side of the diff" — distinct from the
 *  literal string "null" a JSON `null` value would otherwise produce. */
function formatDiffValue(v: unknown): string {
  if (v === undefined) return '—';
  if (v === null) return 'null';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

/**
 * Flatten two (possibly nested) before/after dicts into a list of changed
 * leaf paths, e.g. `["capabilities.write_level: none → write"]`. Nested
 * plain objects (the `chat`/`capabilities` subtrees, and `capabilities.media`
 * one level deeper) recurse into dotted paths; a key present on only one
 * side is treated as the other side's empty object so its leaves still
 * surface individually rather than collapsing into one opaque line.
 */
export function flattenDiff(
  before: Record<string, unknown>,
  after: Record<string, unknown>,
  prefix = '',
): string[] {
  const keys = new Set([
    ...Object.keys(before ?? {}),
    ...Object.keys(after ?? {}),
  ]);
  const diffs: string[] = [];
  for (const key of keys) {
    const path = prefix ? `${prefix}.${key}` : key;
    const beforeVal = (before ?? {})[key];
    const afterVal = (after ?? {})[key];
    const beforeIsObj = isPlainObject(beforeVal);
    const afterIsObj = isPlainObject(afterVal);
    if (beforeIsObj || afterIsObj) {
      diffs.push(
        ...flattenDiff(
          beforeIsObj ? beforeVal : {},
          afterIsObj ? afterVal : {},
          path,
        ),
      );
      continue;
    }
    if (beforeVal !== afterVal) {
      diffs.push(`${path}: ${formatDiffValue(beforeVal)} → ${formatDiffValue(afterVal)}`);
    }
  }
  return diffs;
}

export default function PermissionChangeLog({ audits, loading }: Props) {
  const { t } = useTranslation();

  return (
    <div className="mt-8" data-testid="permission-change-log">
      <h3 className="text-sm font-semibold text-content">
        {t('aiLibrary.permissions.changeLogTitle')}
      </h3>
      <p className="text-xs text-content-3 mt-1 mb-2">
        {t('aiLibrary.permissions.changeLogIntro')}
      </p>

      {loading ? (
        <div className="text-xs text-content-3">{t('common.loading')}</div>
      ) : audits.length === 0 ? (
        <div className="text-xs text-content-3">
          {t('aiLibrary.permissions.changeLogEmpty')}
        </div>
      ) : (
        <ul className="space-y-2">
          {audits.map((audit) => {
            const diffs = flattenDiff(audit.before, audit.after);
            return (
              <li
                key={audit.id}
                className="rounded-md border border-line px-3 py-2 text-xs"
              >
                <div className="flex items-center justify-between gap-2 text-content-3">
                  <span>{new Date(audit.created_at).toLocaleString()}</span>
                  <span className="font-mono">{audit.changed_by.slice(0, 8)}</span>
                </div>
                {diffs.length > 0 && (
                  <ul className="mt-1 space-y-0.5 text-content">
                    {diffs.map((d) => (
                      <li key={d}>{d}</li>
                    ))}
                  </ul>
                )}
                {audit.reason && (
                  <div className="mt-1 italic text-content-3">{audit.reason}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
