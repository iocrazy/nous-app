/**
 * One object's versions, as content (harness 3a §5).
 *
 * Opens on the latest change — the newest version against the one it
 * replaced — and any other version can be picked from the chips. Text
 * versions are diffed word by word; media versions are two thumbnails, and
 * always v1 in this phase (a regeneration makes a new row, so a media object
 * with two versions is possible but never assumed).
 *
 * A side the backend could not reconstruct draws a labelled placeholder, NOT
 * an empty pane: `available:false` means "the snapshot is gone", which is a
 * different fact from "this version was empty" and the reader must be able to
 * tell them apart.
 */
import React, { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useTranslation } from 'react-i18next';
import { History, PlayCircle, RotateCcw, X } from 'lucide-react';

import {
  getOutputDiff,
  getOutputLineage,
  invalidateOutputLineage,
  lineageGeneration,
  subscribeLineageChange,
  OutputsError,
  resolveMediaUrl,
  revertOutput,
  type OutputDiff,
  type OutputDiffSide,
  type OutputVersion,
} from '../../services/outputsService';
import { formatOutputCost, outputCostTitle, type CostKind } from '../agentActivity/outputCost';
import { useOptionalToast } from '../Toast';
import { useChildRun } from './childRunContext';
import { nextLocalSeq, notifyTurn } from './issueTurnSignal';
import { diffWords, type DiffResult, type DiffSegment } from './outputDiff';

export interface OutputDiffDialogProps {
  kind: string;
  refId: string;
  /** Shown in the header when the caller already knows it. */
  title?: string | null;
  /** The newer side to open on; defaults to the latest registered version. */
  initialTo?: number;
  /** Pin the older side too — `initialFrom === initialTo` is how a caller asks
   *  for ONE version on its own rather than a comparison. Cleared as soon as
   *  the reader picks another version from the chips. */
  initialFrom?: number;
  onClose: () => void;
}

type T = (key: string, fallback: string, vars?: Record<string, unknown>) => string;

/** Why a version cannot be shown — the backend's own three reasons. */
function unavailableText(reason: string | null, t: T): string {
  switch (reason) {
    case 'no_snapshot':
      return t('outputs.noSnapshot', 'No snapshot was kept for this version');
    case 'no_ledger':
      return t('outputs.noLedger', 'No ledger entry records this version’s content');
    case 'not_found':
      return t('outputs.notFound', 'The object this version points at is gone');
    default:
      return t('outputs.unavailable', 'This version could not be reconstructed');
  }
}

function errorText(err: unknown, t: T): string {
  if (err instanceof OutputsError) {
    if (err.code === 'not_registered') {
      return t('outputs.errorNotRegistered', 'This object is not in the deliverable registry — nothing recorded producing it.');
    }
    if (err.code === 'version_not_found') {
      return t('outputs.errorVersionNotFound', 'That version is not in this object’s chain.');
    }
    return t('outputs.errorCode', 'Could not read this output ({{code}})', { code: err.code });
  }
  return t('outputs.errorGeneric', 'Could not read this output.');
}

// 只有文本类有账本 / 逆操作批可以重放；媒体回退与媒体版本链是另立的一期（3b §6）。
const REVERTIBLE = new Set(['script_shot', 'script_scene']);

/** 回退失败的话术：每个码说清「发生了什么」和「接下来做什么」。
 *
 *  The facts come from `err.details`, not from `err.message`: the server's
 *  prose is for a log, and the one thing the reader needs — WHICH version
 *  appeared — only exists in the typed payload. */
function revertErrorText(err: unknown, from: number | null, t: T): string {
  if (!(err instanceof OutputsError)) return t('outputs.revertFailed', 'Revert failed.');
  const d = err.details ?? {};
  switch (err.code) {
    case 'version_conflict':
      return t('outputs.revertConflict', 'Someone registered v{{n}} meanwhile — reopen to see it', {
        n: String(d.latest_version ?? '?'),
      });
    case 'content_unavailable':
      return t('outputs.revertNoLedger', "v{{n}} can't be rebuilt (no ledger)", { n: from ?? '?' });
    case 'kind_not_revertible':
      return t('outputs.revertKindRefused', "This kind can't be reverted");
    case 'version_not_found':
      return t('outputs.errorVersionNotFound', 'That version is not in this object’s chain.');
    case 'not_permitted':
      return t('outputs.revertNotPermitted', 'You do not have permission to revert this object.');
    default:
      return t('outputs.revertFailedCode', 'Revert failed ({{code}})', { code: err.code });
  }
}

/** A version number the diff endpoint will actually accept (B4).
 *
 *  `from` / `to` are `Query(..., ge=1)` on the backend, and the chain is the
 *  only source of them. A chain that answers without one leaves `to` as
 *  `undefined` — which is not `null`, so a plain null-check waves it through
 *  and the dialog asks for `?from=undefined&to=undefined`. A request that
 *  cannot succeed is worse than no request: it turns "I could not read the
 *  chain" into a 422 the reader has to decode. */
const usableVersion = (n: unknown): n is number => typeof n === 'number' && Number.isInteger(n) && n >= 1;

const TONE: Record<DiffSegment['type'], string> = {
  same: '',
  add: 'bg-ok-soft text-ok rounded-sm',
  del: 'bg-danger-soft text-danger rounded-sm line-through',
  // Neither side's content — it is the panel admitting a cut. Muted and
  // centred so it reads as a rule across the text, not as one more word.
  omit: 'block text-center text-[11px] text-ink-500 italic select-none',
};

/** One pane of the diff: the segments belonging to this side, in order.
 *
 *  An `omit` segment belongs to NEITHER side, so it survives both filters —
 *  a truncation drawn on only one pane is a truncation the other pane still
 *  hides (C9). */
const Pane: React.FC<{ result: DiffResult; side: 'from' | 'to'; testId: string; t: T }> = ({
  result,
  side,
  testId,
  t,
}) => {
  const skip = side === 'from' ? 'add' : 'del';
  return (
    <div
      data-testid={testId}
      className="max-h-[52vh] min-w-0 overflow-auto whitespace-pre-wrap break-words rounded border border-ink-800 bg-ink-900/60 p-2 text-[12px] leading-relaxed text-ink-200"
    >
      {result.segments
        // A side that lost NOTHING draws no marker: the two sides are cut at
        // the same point but rarely lose the same amount, and "0 lines
        // omitted" is a rule across the text announcing a cut that, for this
        // side, did not happen.
        .filter((s) => s.type !== skip && !(s.type === 'omit' && (s.omitted?.[side] ?? 0) === 0))
        .map((s, i) => (
          <span
            key={`${s.type}:${i}`}
            data-diff={s.type}
            data-testid={s.type === 'omit' ? 'output-diff-omitted' : undefined}
            className={TONE[s.type]}
          >
            {s.type === 'omit'
              ? t('outputs.omitted', '… {{n}} lines omitted here …', { n: s.omitted?.[side] ?? 0 })
              : s.text}
          </span>
        ))}
    </div>
  );
};

const SideHead: React.FC<{ side: OutputDiffSide; label: string; cost: string; costTitle?: string; testId: string }> = ({
  side,
  label,
  cost,
  costTitle,
  testId,
}) => (
  <div className="flex items-baseline gap-2 text-[11px] text-ink-500">
    <span className="font-mono tracking-wider text-ink-400">{label}</span>
    {side.model && <span className="truncate">{side.model}</span>}
    {/* The chain's price, not the diff side's: the registry row is where
        `cost_kind` lives, and the two must not disagree on screen. */}
    <span data-testid={testId} title={costTitle} className="shrink-0 tabular-nums">
      {cost}
    </span>
    {side.created_at && <span className="ml-auto shrink-0 tabular-nums">{side.created_at.slice(0, 16).replace('T', ' ')}</span>}
  </div>
);

const Unavailable: React.FC<{ side: OutputDiffSide; t: T }> = ({ side, t }) => (
  <div
    data-testid="output-diff-unavailable"
    data-reason={side.unavailable_reason ?? 'unknown'}
    className="rounded border border-warn-line bg-warn-soft/40 p-3 text-[12px] text-warn"
  >
    {unavailableText(side.unavailable_reason, t)}
  </div>
);

const MediaSide: React.FC<{ side: OutputDiffSide; t: T }> = ({ side, t }) => {
  // The wire carries these relative; resolve before they reach the DOM.
  const src = resolveMediaUrl(side.media?.cover_url) ?? resolveMediaUrl(side.media?.stream_url);
  if (!src) {
    return (
      <div className="rounded border border-ink-800 bg-ink-900/60 p-3 text-[12px] text-ink-500">
        {t('outputs.noPreview', 'No preview for this version')}
      </div>
    );
  }
  return (
    <img
      data-testid="output-diff-media"
      src={src}
      alt={side.title ?? `v${side.version}`}
      className="max-h-[46vh] w-full rounded border border-ink-800 object-contain"
    />
  );
};

export const OutputDiffDialog: React.FC<OutputDiffDialogProps> = ({ kind, refId, title, initialTo, initialFrom, onClose }) => {
  const { t } = useTranslation();
  const tr = t as unknown as T;
  // The run that produced the newer side — the same affordance a sub-agent
  // card offers, through the same context. Outside a provider (the dialog can
  // be opened from the rail, where no run panel is mounted) it draws disabled
  // rather than vanishing, so the reader knows the run is knowable.
  const childRun = useChildRun();
  const [versions, setVersions] = useState<OutputVersion[]>([]);
  const [to, setTo] = useState<number | null>(initialTo ?? null);
  const [pinnedFrom, setPinnedFrom] = useState<number | null>(initialFrom ?? null);
  const [diff, setDiff] = useState<OutputDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const panel = useRef<HTMLDivElement>(null);
  // Someone ELSE can move this chain while the dialog is open (an agent run
  // finishing, a revert in another pane). The generation is how that reaches a
  // component that holds no reference to the cache.
  const gen = useSyncExternalStore(subscribeLineageChange, lineageGeneration, () => 0);

  // ---- revert (3b §3.4) --------------------------------------------------
  // `useOptionalToast`, because this dialog also mounts from the canvas, where
  // no ToastProvider is above it — a required one would throw there.
  const toast = useOptionalToast();
  const [confirming, setConfirming] = useState(false);
  const [reverting, setReverting] = useState(false);
  const [kept, setKept] = useState<OutputVersion | null>(null);
  const confirmBtn = useRef<HTMLButtonElement>(null);

  // A question just appeared where the button was; focus its answer so Enter
  // resolves the thing the reader is looking at rather than re-firing the
  // button that asked.
  useEffect(() => {
    if (confirming) confirmBtn.current?.focus();
  }, [confirming]);

  // Focus the panel, hand focus back to the opener when it goes.
  useEffect(() => {
    const opener = typeof document !== 'undefined' ? (document.activeElement as HTMLElement | null) : null;
    panel.current?.focus();
    return () => opener?.focus?.();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    let live = true;
    getOutputLineage(kind, refId)
      .then((chain) => {
        if (!live) return;
        // The chain's own answer for "which version do we open on", or
        // nothing at all — never `undefined` masquerading as a number.
        const latest = usableVersion(chain.latest_version)
          ? chain.latest_version
          : chain.versions?.find((v) => usableVersion(v.version))?.version ?? null;
        setVersions(chain.versions ?? []);
        setTo((cur) => (usableVersion(cur) ? cur : latest));
        if (latest === null && !usableVersion(initialTo)) {
          // Nothing to compare and nothing to show: say so and stop the
          // spinner, rather than leaving «Loading…» up for ever.
          setError(errorText(null, tr));
          setLoading(false);
          return;
        }
        setError(null);
      })
      .catch((err) => {
        if (!live) return;
        console.error('[OutputDiffDialog] lineage failed', err);
        setError(errorText(err, tr));
        setLoading(false);
      });
    return () => {
      live = false;
    };
    // `tr` is a fresh function each render; the copy is picked at throw time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, refId, gen]);

  // The older side of the pair: the version this one actually replaced. A
  // first version has no predecessor, so it is compared with itself and drawn
  // as a single pane — never against a version that does not exist.
  const from = useMemo(() => {
    if (pinnedFrom !== null) return pinnedFrom;
    if (to === null) return null;
    const row = versions.find((v) => v.version === to);
    const parent = row?.parent_version ?? null;
    if (parent !== null && versions.some((v) => v.version === parent)) return parent;
    return versions.some((v) => v.version === to - 1) ? to - 1 : to;
  }, [versions, to, pinnedFrom]);

  const latestVersion = versions.length ? Math.max(...versions.map((x) => x.version)) : 0;
  const canRevert = REVERTIBLE.has(kind) && from !== null && from < latestVersion && !reverting;
  const row = (n: number | null): OutputVersion | null => (n === null ? null : versions.find((x) => x.version === n) ?? null);
  const costOf = (n: number): string => formatOutputCost(row(n)?.cost_cents ?? null, (row(n)?.cost_kind ?? null) as CostKind);
  /** Why that number reads the way it does — `≈` says "approximate", this says
   *  what it was approximated FROM; on an unpriced media row it names the model
   *  whose catalogue price is missing. `undefined` when there is nothing to add. */
  const costTitleOf = (n: number): string | undefined =>
    outputCostTitle(row(n)?.cost_cents ?? null, (row(n)?.cost_kind ?? null) as CostKind, {
      deliverableKind: kind,
      model: row(n)?.model ?? null,
    });

  const doRevert = async (): Promise<void> => {
    if (from === null) return;
    setReverting(true);
    try {
      const res = await revertOutput(kind, refId, { toVersion: from, expectedLatest: latestVersion });
      // 用响应里的新版就地更新，不等重拉（3b §4）；同时让缓存的链作废，
      // 别处（来源块、右栏）下一次读才拿到真答案。
      setVersions((prev) => [res.version, ...(res.kept_version ? [res.kept_version] : []), ...prev]);
      setKept(res.kept_version);
      setPinnedFrom(null);
      setTo(res.version.version);
      setConfirming(false);
      invalidateOutputLineage(kind, refId);
      // The issue page has to re-read too. The reverted version carries its own
      // issue when it has one; otherwise borrow the one already on the chain,
      // and when nothing on the chain answers to an issue (the canvas), say
      // nothing — there is no issue page to refresh.
      //
      // ⚠️ The seq is a LOCAL counter, never the new version's id. That id is a
      // Snowflake: `Number()` rounds it past 2^53, so two reverts minted in the
      // same millisecond collapse to one value and the watermark drops the
      // second as a replay. `runId: null` puts it in its own lane, where a
      // counter that only has to beat its own last value is all that is needed.
      const signalIssue = res.version.issue_id ?? versions[0]?.issue_id ?? null;
      if (signalIssue) notifyTurn(String(signalIssue), { runId: null, seq: nextLocalSeq() });
      toast?.addToast(tr('outputs.revertDone', 'Reverted to v{{from}} as v{{n}}', { from, n: res.version.version }), 'success');
    } catch (err) {
      console.error('[OutputDiffDialog] revert failed', err);
      toast?.addToast(revertErrorText(err, from, tr), 'error');
      setConfirming(false);
    } finally {
      setReverting(false);
    }
  };

  useEffect(() => {
    // Both ends must be numbers the endpoint accepts (B4) — `null` is the
    // "not known yet" case and `undefined` the "chain never said" one.
    if (!usableVersion(to) || !usableVersion(from)) return;
    let live = true;
    setLoading(true);
    getOutputDiff(kind, refId, from, to)
      .then((body) => {
        if (!live) return;
        setDiff(body);
        setError(null);
      })
      .catch((err) => {
        if (!live) return;
        console.error('[OutputDiffDialog] diff failed', err);
        setError(errorText(err, tr));
        setDiff(null);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, refId, from, to]);

  const single = !!diff && diff.from.version === diff.to.version;
  const text = useMemo(
    () => (diff && diff.content_type === 'text' ? diffWords(diff.from.text ?? '', diff.to.text ?? '') : null),
    [diff],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/70 p-4"
      data-testid="output-diff-backdrop"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="output-diff-title"
        data-testid="output-diff"
        ref={panel}
        tabIndex={-1}
        className="w-[min(920px,94vw)] rounded-lg border border-ink-800 bg-ink-950 p-4 shadow-xl focus:outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2">
          <History size={14} className="text-info" />
          <h2 id="output-diff-title" className="min-w-0 truncate text-[14px] font-medium text-ink-100">
            {title ?? diff?.to.title ?? `${kind} #${refId}`}
          </h2>
          <span className="shrink-0 text-[11px] text-ink-500">{kind.replace(/_/g, ' ')}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('common.close', 'Close')}
            className="ml-auto text-ink-500 hover:text-ink-200"
          >
            <X size={14} />
          </button>
        </div>

        {versions.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1" data-testid="output-diff-versions">
            {versions.map((v) => (
              <button
                key={v.version}
                type="button"
                data-testid={`output-diff-version-${v.version}`}
                onClick={() => {
                  setPinnedFrom(null);
                  setTo(v.version);
                  // "your edits were kept as v3" answers a question about the
                  // revert just performed; carried onto another version it
                  // becomes a claim about the wrong object. Cleared here
                  // rather than in the diff effect, because `doRevert` moves
                  // `to` itself and would wipe the note it just earned.
                  setKept(null);
                  setConfirming(false);
                }}
                className={`rounded border px-1.5 py-0.5 text-[11px] ${
                  v.version === to ? 'border-info-line bg-info-soft text-info' : 'border-ink-700 text-ink-400 hover:border-ink-500'
                }`}
              >
                {t('outputs.version', 'v{{n}}', { n: v.version })}
              </button>
            ))}
          </div>
        )}

        {error && (
          <p data-testid="output-diff-error" className="mt-3 break-words text-[12px] text-danger">
            {error}
          </p>
        )}

        {!error && loading && !diff && (
          <p className="mt-3 text-[12px] text-ink-500">{t('common.loading', 'Loading…')}</p>
        )}

        {!error && diff && (
          <div className={`mt-3 grid gap-3 ${single ? 'grid-cols-1' : 'sm:grid-cols-2'}`}>
            {!single && (
              <div className="min-w-0 space-y-1">
                <SideHead
                  side={diff.from}
                  label={t('outputs.version', 'v{{n}}', { n: diff.from.version })}
                  cost={costOf(diff.from.version)}
                  costTitle={costTitleOf(diff.from.version)}
                  testId="output-diff-cost-from"
                />
                {!diff.from.available ? (
                  <Unavailable side={diff.from} t={tr} />
                ) : diff.content_type === 'media' ? (
                  <MediaSide side={diff.from} t={tr} />
                ) : (
                  text && <Pane result={text} side="from" testId="output-diff-from" t={tr} />
                )}
              </div>
            )}
            <div className="min-w-0 space-y-1" data-testid={single ? 'output-diff-only' : undefined}>
              <SideHead
                side={diff.to}
                label={t('outputs.version', 'v{{n}}', { n: diff.to.version })}
                cost={costOf(diff.to.version)}
                costTitle={costTitleOf(diff.to.version)}
                testId="output-diff-cost-to"
              />
              {!diff.to.available ? (
                <Unavailable side={diff.to} t={tr} />
              ) : diff.content_type === 'media' ? (
                <MediaSide side={diff.to} t={tr} />
              ) : (
                text && <Pane result={text} side="to" testId={single ? 'output-diff-single-text' : 'output-diff-to'} t={tr} />
              )}
            </div>
          </div>
        )}

        {text?.truncated && (
          <p data-testid="output-diff-truncated" className="mt-2 text-[11px] text-warn">
            {t('outputs.truncated', 'This text is too long to compare in full — the middle of the change is left out, marked where it was cut.')}
          </p>
        )}

        {/* `flex-wrap`, so the "edits were kept" line below can take its own
            row (`w-full`) instead of being squeezed into the button row. */}
        <div className="mt-3 flex flex-wrap items-center gap-3">
          {diff && (
            <button
              type="button"
              data-testid="output-open-run"
              disabled={!childRun}
              title={childRun ? undefined : t('outputs.openRunHint', 'The run panel is not open here')}
              onClick={() =>
                childRun?.open({
                  childRunId: diff.to.run_id,
                  parentRunId: null,
                  // The coordinate the registry kept for this version; 0 when
                  // the chain does not carry one (the panel only labels it).
                  step: versions.find((v) => v.version === diff.to.version)?.step ?? 0,
                  mode: 'sync',
                  subagentType: kind.replace(/_/g, ' '),
                  description: diff.to.title ?? `${kind} #${refId}`,
                })
              }
              className="inline-flex items-center gap-1 rounded border border-ink-700 px-2 py-1 text-[12px] text-ink-300 hover:border-info-line hover:text-info disabled:cursor-not-allowed disabled:opacity-50"
            >
              <PlayCircle size={12} />
              {t('outputs.openRun', 'Open Run #{{run}}', { run: diff.to.run_id.slice(-6) })}
            </button>
          )}
          {text && !single && (
            <span className="text-[11px] text-ink-500 tabular-nums">
              {t('outputs.changeCount', '+{{added}} / −{{removed}} words', { added: text.added, removed: text.removed })}
            </span>
          )}
          {/* 回退版的身份：v4 ↩ v1 · Reverted · You。info 色（不是 ok/warn）——
              回退是一次导航，既不是成功也不是告警。 */}
          {row(to)?.reverted_from_version != null && (
            <span
              data-testid="output-reverted-chip"
              className="rounded border border-info-line bg-info-soft px-1.5 py-0.5 text-[11px] text-info tabular-nums"
            >
              {t('outputs.revertedChip', 'v{{n}} ↩ v{{from}}', { n: to, from: row(to)?.reverted_from_version })}
              <span className="ml-1.5">{t('outputs.revertedBy', 'Reverted · You')}</span>
            </span>
          )}
          {confirming ? (
            <span data-testid="output-revert-confirm" className="ml-auto flex items-center gap-2 text-[12px] text-ink-300">
              {t('outputs.revertConfirm', 'Revert to v{{from}}? This creates v{{next}}.', { from, next: latestVersion + 1 })}
              <button
                type="button"
                data-testid="output-revert-go"
                ref={confirmBtn}
                disabled={reverting}
                onClick={() => void doRevert()}
                className="rounded border border-info-line px-2 py-1 text-info disabled:opacity-50"
              >
                {t('outputs.revertGo', 'Revert')}
              </button>
              <button
                type="button"
                data-testid="output-revert-cancel"
                onClick={() => setConfirming(false)}
                className="rounded border border-ink-700 px-2 py-1 text-ink-400"
              >
                {t('common.cancel', 'Cancel')}
              </button>
            </span>
          ) : (
            <button
              type="button"
              data-testid="output-diff-revert"
              disabled={!canRevert}
              title={canRevert ? undefined : t('outputs.revertHint', 'Only an older script version can be reverted')}
              onClick={() => setConfirming(true)}
              className="ml-auto inline-flex items-center gap-1 rounded border border-ink-700 px-3 py-1.5 text-[13px] text-ink-300 hover:border-info-line hover:text-info disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RotateCcw size={12} />
              {t('outputs.revert', 'Revert To v{{n}}', { n: from ?? 1 })}
            </button>
          )}
          {kept && (
            <p data-testid="output-revert-kept" className="mt-2 w-full text-[11px] text-info">
              {t('outputs.revertKept', 'Your edits before the revert were kept as v{{n}}.', { n: kept.version })}
            </p>
          )}
        </div>
      </div>
    </div>
  );
};
