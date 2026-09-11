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
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { History, PlayCircle, X } from 'lucide-react';

import {
  getOutputDiff,
  getOutputLineage,
  OutputsError,
  resolveMediaUrl,
  type OutputDiff,
  type OutputDiffSide,
  type OutputVersion,
} from '../../services/outputsService';
import { useChildRun } from './childRunContext';
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

const TONE: Record<DiffSegment['type'], string> = {
  same: '',
  add: 'bg-ok-soft text-ok rounded-sm',
  del: 'bg-danger-soft text-danger rounded-sm line-through',
};

/** One pane of the diff: the segments belonging to this side, in order. */
const Pane: React.FC<{ result: DiffResult; side: 'from' | 'to'; testId: string }> = ({ result, side, testId }) => {
  const skip = side === 'from' ? 'add' : 'del';
  return (
    <div
      data-testid={testId}
      className="max-h-[52vh] min-w-0 overflow-auto whitespace-pre-wrap break-words rounded border border-ink-800 bg-ink-900/60 p-2 text-[12px] leading-relaxed text-ink-200"
    >
      {result.segments
        .filter((s) => s.type !== skip)
        .map((s, i) => (
          <span key={`${s.type}:${i}`} data-diff={s.type} className={TONE[s.type]}>
            {s.text}
          </span>
        ))}
    </div>
  );
};

const SideHead: React.FC<{ side: OutputDiffSide; label: string }> = ({ side, label }) => (
  <div className="flex items-baseline gap-2 text-[11px] text-ink-500">
    <span className="font-mono tracking-wider text-ink-400">{label}</span>
    {side.model && <span className="truncate">{side.model}</span>}
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
        setVersions(chain.versions);
        setTo((cur) => cur ?? chain.latest_version);
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
  }, [kind, refId]);

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

  useEffect(() => {
    if (to === null || from === null) return;
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
                <SideHead side={diff.from} label={t('outputs.version', 'v{{n}}', { n: diff.from.version })} />
                {!diff.from.available ? (
                  <Unavailable side={diff.from} t={tr} />
                ) : diff.content_type === 'media' ? (
                  <MediaSide side={diff.from} t={tr} />
                ) : (
                  text && <Pane result={text} side="from" testId="output-diff-from" />
                )}
              </div>
            )}
            <div className="min-w-0 space-y-1" data-testid={single ? 'output-diff-only' : undefined}>
              <SideHead side={diff.to} label={t('outputs.version', 'v{{n}}', { n: diff.to.version })} />
              {!diff.to.available ? (
                <Unavailable side={diff.to} t={tr} />
              ) : diff.content_type === 'media' ? (
                <MediaSide side={diff.to} t={tr} />
              ) : (
                text && <Pane result={text} side="to" testId={single ? 'output-diff-single-text' : 'output-diff-to'} />
              )}
            </div>
          </div>
        )}

        {text?.truncated && (
          <p data-testid="output-diff-truncated" className="mt-2 text-[11px] text-warn">
            {t('outputs.truncated', 'This text is too long to compare in full — only the first part of the change is shown.')}
          </p>
        )}

        <div className="mt-3 flex items-center gap-3">
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
          <button
            type="button"
            data-testid="output-diff-revert"
            disabled
            title={t('outputs.revertHint', 'Arrives with 3b')}
            className="ml-auto cursor-not-allowed rounded border border-ink-700 px-3 py-1.5 text-[13px] text-ink-500 opacity-50"
          >
            {t('outputs.revert', 'Revert To v{{n}}', { n: diff?.from.version ?? 1 })}
          </button>
        </div>
      </div>
    </div>
  );
};
