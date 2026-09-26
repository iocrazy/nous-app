// Settings → AI → Vectors → Indexing Policy: the shot-index automation
// (spec 2026-09-26 §3.5). Reads `status.shots_policy`; writes only the fields
// the admin changed (`PUT /search/vectors/shots-policy`). Non-admins see the
// same controls read-only. `local_only` on a network provider equals Off —
// the grey line says so instead of leaving the admin to wonder.
import { useEffect, useState } from 'react';

import type { ShotsPolicyStatus, ShotsPolicyUpdate } from '../../types/api';

type TFn = (key: string, opts?: Record<string, unknown>) => string;
type Mode = ShotsPolicyStatus['auto_index'];

const MODES: Mode[] = ['off', 'local_only', 'always'];
const NUM = new Intl.NumberFormat('en-US');
const BATCH_RANGE = { min: 1, max: 50 };
const DAILY_CAP_RANGE = { min: 0, max: 10_000 };

interface IndexingPolicySectionProps {
  policy: ShotsPolicyStatus;
  /** Videos still without a frame vector, from the Visual row (total − covered). */
  remaining: number | null;
  canManage: boolean;
  busy: boolean;
  t: TFn;
  notice: string | null;
  error: string | null;
  onSave: (patch: ShotsPolicyUpdate) => void;
}

function modeLabel(mode: Mode, t: TFn): string {
  if (mode === 'always') return t('settings.vectors.policyAlways');
  if (mode === 'local_only') return t('settings.vectors.policyLocalOnly');
  return t('settings.vectors.policyOff');
}

function ModePicker({
  id,
  value,
  disabled,
  t,
  onChange,
}: {
  id: string;
  value: Mode;
  disabled: boolean;
  t: TFn;
  onChange: (m: Mode) => void;
}) {
  return (
    <span
      className="inline-flex overflow-hidden rounded-lg border border-ink-700"
      role="radiogroup"
      data-testid={`policy-${id}`}
    >
      {MODES.map((m) => (
        <button
          key={m}
          type="button"
          role="radio"
          aria-checked={value === m}
          disabled={disabled}
          className={`px-2.5 py-1 text-xs disabled:cursor-not-allowed ${
            value === m ? 'bg-info/10 text-info' : 'text-ink-300 hover:bg-ink-800'
          }`}
          onClick={() => onChange(m)}
        >
          {modeLabel(m, t)}
        </button>
      ))}
    </span>
  );
}

function clampInt(raw: string, lo: number, hi: number, fallback: number): number {
  const n = Number.parseInt(raw, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.max(lo, Math.min(hi, n));
}

export function IndexingPolicySection({
  policy,
  remaining,
  canManage,
  busy,
  t,
  notice,
  error,
  onSave,
}: IndexingPolicySectionProps) {
  const [draft, setDraft] = useState<ShotsPolicyUpdate>({});
  // A fresh status (after Save, or a refetch) resets the draft to "no edits".
  useEffect(() => {
    setDraft({});
  }, [policy]);

  const autoIndex = draft.auto_index ?? policy.auto_index;
  const backfill = draft.backfill ?? policy.backfill;
  const batch = draft.batch ?? policy.batch;
  const dailyCap = draft.daily_cap ?? policy.daily_cap;
  const dirty = Object.keys(draft).length > 0;
  const readOnly = !canManage || busy;
  const localOnlyInert = policy.provider_local !== true;

  let providerLine: string;
  let providerWarn = false;
  if (policy.provider_local === true) providerLine = t('settings.vectors.policyProviderLocal');
  else if (policy.provider_local === false) {
    providerLine = t('settings.vectors.policyProviderNetwork');
    providerWarn = true;
  } else {
    providerLine = t('settings.vectors.policyProviderUnknown');
    providerWarn = true;
  }
  // The provider line matters when either mode relies on "local".
  const showProviderLine = policy.provider_local === true || autoIndex === 'local_only' || backfill === 'local_only';

  const input =
    'w-20 rounded-lg border border-ink-700 bg-ink-950/60 px-2 py-0.5 text-xs tabular-nums text-ink-100 disabled:cursor-not-allowed disabled:opacity-60';

  return (
    <section className="rounded-xl border border-ink-800 bg-ink-900/40 p-4 space-y-3" data-testid="indexing-policy">
      <h3 className="text-sm font-semibold text-ink-100">{t('settings.vectors.policyTitle')}</h3>
      <dl className="grid grid-cols-[10rem_1fr] items-center gap-x-3 gap-y-2 text-sm">
        <dt className="text-xs text-ink-500">{t('settings.vectors.policyAutoIndex')}</dt>
        <dd className="flex flex-wrap items-center gap-2">
          <ModePicker
            id="auto-index"
            value={autoIndex}
            disabled={readOnly}
            t={t}
            onChange={(m) => setDraft((d) => ({ ...d, auto_index: m }))}
          />
        </dd>
        <dt className="text-xs text-ink-500">{t('settings.vectors.policyBackfill')}</dt>
        <dd className="flex flex-wrap items-center gap-2">
          <ModePicker
            id="backfill"
            value={backfill}
            disabled={readOnly}
            t={t}
            onChange={(m) => setDraft((d) => ({ ...d, backfill: m }))}
          />
        </dd>
        {showProviderLine && (
          <>
            <dt />
            <dd
              data-testid="policy-provider-line"
              className={`text-xs ${providerWarn && localOnlyInert ? 'text-warn' : 'text-ink-400'}`}
            >
              {providerLine}
            </dd>
          </>
        )}
        <dt className="text-xs text-ink-500">{t('settings.vectors.policyBatch')}</dt>
        <dd className="flex flex-wrap items-center gap-2">
          <input
            type="number"
            aria-label={t('settings.vectors.policyBatch')}
            className={input}
            min={BATCH_RANGE.min}
            max={BATCH_RANGE.max}
            value={batch}
            disabled={readOnly}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                batch: clampInt(e.target.value, BATCH_RANGE.min, BATCH_RANGE.max, policy.batch),
              }))
            }
          />
          <span className="text-xs text-ink-400">{t('settings.vectors.policyBatchHint', { count: batch })}</span>
        </dd>
        <dt className="text-xs text-ink-500">{t('settings.vectors.policyDailyCap')}</dt>
        <dd className="flex flex-wrap items-center gap-2">
          <input
            type="number"
            aria-label={t('settings.vectors.policyDailyCap')}
            className={input}
            min={DAILY_CAP_RANGE.min}
            max={DAILY_CAP_RANGE.max}
            value={dailyCap}
            disabled={readOnly}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                daily_cap: clampInt(e.target.value, DAILY_CAP_RANGE.min, DAILY_CAP_RANGE.max, policy.daily_cap),
              }))
            }
          />
          <span className="text-xs text-ink-400">
            {t('settings.vectors.policyDailyCapHint')} · {t('settings.vectors.policyDailyCapUnlimited')}
          </span>
        </dd>
        <dt className="text-xs text-ink-500">{t('settings.vectors.policyToday')}</dt>
        <dd className="text-xs text-ink-300" data-testid="policy-today">
          <span className="tabular-nums">
            {t('settings.vectors.policyTodayLine', {
              dispatched: NUM.format(policy.dispatched_today),
              active: NUM.format(policy.active),
              remaining: remaining === null ? '—' : NUM.format(remaining),
            })}
          </span>
          <span className="text-ink-500">
            {' · '}
            {policy.last_tick
              ? t('settings.vectors.policyLastTick', {
                  time: policy.last_tick.replace('T', ' ').slice(0, 16),
                })
              : t('settings.vectors.policyNoTick')}
          </span>
          {policy.last_skip && !policy.last_error && (
            <span className="text-ink-500">
              {' · '}
              {t('settings.vectors.policyLastSkip', {
                reason: policy.last_skip,
              })}
            </span>
          )}
          {policy.last_error && (
            <span className="text-warn" data-testid="policy-last-error">
              {' · '}
              {t('settings.vectors.policyLastError', {
                error: policy.last_error,
              })}
            </span>
          )}
        </dd>
      </dl>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="rounded-lg border border-info px-2.5 py-1 text-xs text-info hover:bg-info/10 disabled:cursor-not-allowed disabled:border-ink-800 disabled:text-ink-500"
          disabled={readOnly || !dirty}
          title={canManage ? undefined : t('settings.vectors.policyReadOnly')}
          onClick={() => onSave(draft)}
        >
          {t('settings.vectors.policySave')}
        </button>
        <span className="text-xs text-ink-500">
          {canManage ? t('settings.vectors.policyAdminHint') : t('settings.vectors.policyReadOnly')}
        </span>
      </div>
      {notice && (
        <p className="text-xs text-ok" data-testid="policy-notice">
          {notice}
        </p>
      )}
      {error && (
        <p className="text-xs text-danger" data-testid="policy-error">
          {error}
        </p>
      )}
    </section>
  );
}
