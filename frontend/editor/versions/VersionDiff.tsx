/**
 * VersionDiff — the Cursor-style comparison surface that floats in the margin
 * right of the sheet when the writer hits Compare on a version. It replays the
 * op ledger on the server (via `diffCommit`) and renders the result as a set of
 * per-scene change groups:
 *
 *   * three change states — added (green), removed (red strike), and changed
 *     (word-level inline: the edited element's old text struck red, new text
 *     highlit green, computed by `wordDiff`);
 *   * scene-set adds/removes AGGREGATED into a single collapsible group ("Added
 *     N scenes"), so a fresh script's 15 new scenes are one row, not fifteen;
 *   * author attribution — every change (and every scene group) carries the
 *     actor who made it as a chip, resolved to a display name server-side and
 *     labelled "You" / AI on the client (`authorLabel`);
 *   * click-to-jump — a change scrolls the live sheet to its element (or its
 *     scene when the element is gone); a change whose scene no longer exists is
 *     a non-interactive row.
 *
 * The header names the compared commit against the live 'Current' state and
 * carries Back. Ids are strings end-to-end (#1006). Dual-theme via the shell CSS
 * vars; zero emoji.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  diffCommit,
  type CommitDiff,
  type DiffChange,
  type ScriptCommit,
  type SceneSnapshot,
} from '../sceneService';
import type { SceneDoc } from '../types';
import { authorLabel } from './authorLabel';
import { wordDiff } from './wordDiff';
import { Loading } from '../../components/common/Loading';

export interface VersionDiffProps {
  scriptId: string;
  commit: ScriptCommit;
  /** Commit id to diff against, or 'current' for the live state (default). */
  against?: string;
  /** Display label for the right side of the comparison (default: Current). */
  againstLabel?: string;
  /** Live scenes — the source for a changed scene's heading + jump targets. */
  scenes: SceneDoc[];
  /** The signed-in user id — their changes read as "You". */
  currentUserId?: string | null;
  onBack: () => void;
  /** Scroll + highlight the live sheet at (sceneId, elementId|null). */
  onJumpTo?: (sceneId: string, elementId: string | null) => void;
}

type LoadState = 'loading' | 'ready' | 'error';

/** Scene-set groups collapse by default only when they'd crowd the rail. */
const SCENE_SET_COLLAPSE_THRESHOLD = 3;

/** `INT · Location`, empty parts dropped (mirrors the storyboard column head). */
function snapshotHeading(snap: SceneSnapshot): string {
  return [snap.heading_int_ext, snap.location_text]
    .map((part) => (part ?? '').trim())
    .filter((part) => part.length > 0)
    .join(' · ');
}

function sceneHeading(scene: SceneDoc): string {
  return [scene.heading_int_ext, scene.location_text, scene.time_of_day]
    .map((part) => (part ?? '').trim())
    .filter((part) => part.length > 0)
    .join(' · ');
}

/** Human badge label per change kind. */
const KIND_LABEL: Record<DiffChange['kind'], string> = {
  added: 'editor.diffKindAdded',
  removed: 'editor.diffKindRemoved',
  changed: 'editor.diffKindChanged',
  moved: 'editor.diffKindMoved',
};

type TranslateFn = (key: string, opts?: Record<string, unknown>) => string;

interface AuthorCtx {
  currentUserId?: string | null;
  authors?: Record<string, string>;
  t: TranslateFn;
}

/** A small monochrome author chip; renders nothing when there's no actor. */
function AuthorChip({ actor, ctx }: { actor: string | null | undefined; ctx: AuthorCtx }) {
  const label = authorLabel(actor, ctx);
  if (!label) return null;
  return (
    <span className="mh-diff-author" data-testid="diff-author">
      {label}
    </span>
  );
}

/** The Cursor-style inline body of a 'changed' element: old text struck red,
 *  new text highlit green, aligned at word granularity. */
function ChangedBody({ before, after, t }: { before: string; after: string; t: TranslateFn }) {
  const { before: beforeSegs, after: afterSegs } = useMemo(
    () => wordDiff(before, after),
    [before, after],
  );
  return (
    <div className="mh-diff-texts">
      <div className="mh-diff-text before" data-testid="diff-before">
        {beforeSegs.length === 0
          ? t('editor.diffEmptyLine')
          : beforeSegs.map((s, i) => (
              <span key={i} className={s.kind === 'del' ? 'mh-w-del' : 'mh-w-eq'}>
                {s.text}
              </span>
            ))}
      </div>
      <div className="mh-diff-text after" data-testid="diff-after">
        {afterSegs.length === 0
          ? t('editor.diffEmptyLine')
          : afterSegs.map((s, i) => (
              <span key={i} className={s.kind === 'ins' ? 'mh-w-ins' : 'mh-w-eq'}>
                {s.text}
              </span>
            ))}
      </div>
    </div>
  );
}

/** One element-level change row. */
function ChangeRow({
  change,
  ctx,
  jumpable,
  onJump,
}: {
  change: DiffChange;
  ctx: AuthorCtx;
  jumpable: boolean;
  onJump: () => void;
}) {
  const { t } = ctx;
  const beforeText = change.before?.text ?? '';
  const afterText = change.after?.text ?? '';
  return (
    <li
      className={`mh-diff-change ${change.kind}${jumpable ? ' jumpable' : ''}`}
      data-testid="diff-change"
      data-kind={change.kind}
      role={jumpable ? 'button' : undefined}
      tabIndex={jumpable ? 0 : undefined}
      onClick={jumpable ? onJump : undefined}
      onKeyDown={
        jumpable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onJump();
              }
            }
          : undefined
      }
    >
      <div className="mh-diff-change-head">
        <span className={`mh-diff-badge ${change.kind}`}>{t(KIND_LABEL[change.kind])}</span>
        <AuthorChip actor={change.actor} ctx={ctx} />
      </div>
      {change.kind === 'changed' ? (
        <ChangedBody before={beforeText} after={afterText} t={t} />
      ) : (
        <div className="mh-diff-texts">
          {change.kind === 'removed' && (
            <div className="mh-diff-text before" data-testid="diff-before">
              {beforeText || t('editor.diffEmptyLine')}
            </div>
          )}
          {(change.kind === 'added' || change.kind === 'moved') && (
            <div className="mh-diff-text after" data-testid="diff-after">
              {afterText || t('editor.diffEmptyLine')}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/** A collapsible "Added / Removed N scenes" aggregation group. */
function SceneSetGroup({
  kind,
  snaps,
  ctx,
  jumpableIds,
  onJump,
}: {
  kind: 'added' | 'removed';
  snaps: SceneSnapshot[];
  ctx: AuthorCtx;
  jumpableIds: Set<string>;
  onJump: (sceneId: string) => void;
}) {
  const { t } = ctx;
  const [open, setOpen] = useState(snaps.length <= SCENE_SET_COLLAPSE_THRESHOLD);
  const label =
    kind === 'added' ? 'editor.diffScenesAddedN' : 'editor.diffScenesRemovedN';
  return (
    <section className={`mh-diff-sceneset ${kind}`} data-testid={`diff-sceneset-${kind}`}>
      <button
        type="button"
        className="mh-diff-group-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`mh-diff-caret${open ? ' open' : ''}`} aria-hidden="true">
          ▸
        </span>
        <span className={`mh-diff-badge ${kind}`}>
          {t(kind === 'added' ? 'editor.diffKindAdded' : 'editor.diffKindRemoved')}
        </span>
        <span className="mh-diff-group-label">{t(label, { count: snaps.length })}</span>
        <span className="mh-diff-scene-count">{snaps.length}</span>
      </button>
      {open && (
        <ul className="mh-diff-sceneset-list">
          {snaps.map((snap) => {
            const sid = String(snap.id);
            const jumpable = kind === 'added' && jumpableIds.has(sid);
            return (
              <li
                key={sid}
                className={`mh-diff-sceneset-item ${kind}${jumpable ? ' jumpable' : ''}`}
                data-testid={`diff-scene-${kind}`}
                role={jumpable ? 'button' : undefined}
                tabIndex={jumpable ? 0 : undefined}
                onClick={jumpable ? () => onJump(sid) : undefined}
                onKeyDown={
                  jumpable
                    ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          onJump(sid);
                        }
                      }
                    : undefined
                }
              >
                <span className="mh-diff-scene-name">
                  {snapshotHeading(snap) || t('editor.untitledScene')}
                </span>
                <AuthorChip actor={snap.author} ctx={ctx} />
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function VersionDiff({
  scriptId,
  commit,
  against = 'current',
  againstLabel,
  scenes,
  currentUserId,
  onBack,
  onJumpTo,
}: VersionDiffProps) {
  const { t } = useTranslation();
  const [diff, setDiff] = useState<CommitDiff | null>(null);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // Live scene → element-id set, for jump-target existence + headings.
  const sceneIndex = useMemo(() => {
    const map = new Map<string, { scene: SceneDoc; elementIds: Set<string> }>();
    for (const s of scenes) {
      map.set(String(s.id), {
        scene: s,
        elementIds: new Set((s.elements ?? []).map((e) => String(e.id))),
      });
    }
    return map;
  }, [scenes]);

  const headingFor = useCallback(
    (sceneId: string): string => {
      const entry = sceneIndex.get(String(sceneId));
      const line = entry ? sceneHeading(entry.scene) : '';
      return line || t('editor.untitledScene');
    },
    [sceneIndex, t],
  );

  useEffect(() => {
    let cancelled = false;
    setLoadState('loading');
    diffCommit(scriptId, commit.id, against)
      .then((data) => {
        if (cancelled) return;
        setDiff(data);
        setLoadState('ready');
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[VersionDiff] failed to load diff', err);
        setLoadState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [scriptId, commit.id, against]);

  const authorCtx: AuthorCtx = useMemo(
    () => ({ currentUserId, authors: diff?.authors, t }),
    [currentUserId, diff?.authors, t],
  );

  const jumpToChange = useCallback(
    (sceneId: string, change: DiffChange) => {
      if (!onJumpTo) return;
      const entry = sceneIndex.get(String(sceneId));
      if (!entry) return; // scene is gone — non-interactive
      const elementId = entry.elementIds.has(String(change.id)) ? String(change.id) : null;
      onJumpTo(String(sceneId), elementId);
    },
    [onJumpTo, sceneIndex],
  );

  const toggleScene = useCallback((sceneId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(sceneId)) next.delete(sceneId);
      else next.add(sceneId);
      return next;
    });
  }, []);

  const rightLabel = againstLabel ?? t('editor.diffCurrent');
  const empty =
    diff !== null &&
    diff.scenes.length === 0 &&
    diff.scenes_added.length === 0 &&
    diff.scenes_removed.length === 0;

  return (
    <div className="mh-version-diff" data-testid="version-diff">
      <div className="mh-diff-topbar">
        <button type="button" className="mh-diff-back" onClick={onBack}>
          {t('editor.diffBack')}
        </button>
        <div className="mh-diff-title">
          <span className="mh-diff-side from">{commit.message}</span>
          <span className="mh-diff-arrow" aria-hidden="true">
            →
          </span>
          <span className="mh-diff-side to">{rightLabel}</span>
        </div>
      </div>

      <div className="mh-diff-scroll">
        {loadState === 'loading' && (
          <div className="mh-diff-state" role="status">
            <Loading label={t('editor.diffLoading')} />
          </div>
        )}
        {loadState === 'error' && (
          <div className="mh-diff-state error" role="alert">
            {t('editor.diffLoadError')}
          </div>
        )}
        {loadState === 'ready' && empty && (
          <div className="mh-diff-state">{t('editor.diffNoChanges')}</div>
        )}

        {loadState === 'ready' && diff && !empty && (
          <>
            {diff.scenes_added.length > 0 && (
              <SceneSetGroup
                kind="added"
                snaps={diff.scenes_added}
                ctx={authorCtx}
                jumpableIds={new Set(sceneIndex.keys())}
                onJump={(sid) => onJumpTo?.(sid, null)}
              />
            )}
            {diff.scenes_removed.length > 0 && (
              <SceneSetGroup
                kind="removed"
                snaps={diff.scenes_removed}
                ctx={authorCtx}
                jumpableIds={new Set()}
                onJump={() => undefined}
              />
            )}

            {diff.scenes.map((sceneDiff) => {
              const sid = String(sceneDiff.scene_id);
              const isCollapsed = collapsed.has(sid);
              return (
                <section key={sid} className="mh-diff-scene" data-testid="diff-scene">
                  <button
                    type="button"
                    className="mh-diff-scene-head mh-diff-group-head"
                    aria-expanded={!isCollapsed}
                    onClick={() => toggleScene(sid)}
                  >
                    <span
                      className={`mh-diff-caret${isCollapsed ? '' : ' open'}`}
                      aria-hidden="true"
                    >
                      ▸
                    </span>
                    <span className="mh-diff-scene-heading">{headingFor(sid)}</span>
                    <span className="mh-diff-scene-count">{sceneDiff.elements.length}</span>
                    <AuthorChip actor={sceneDiff.author} ctx={authorCtx} />
                  </button>
                  {!isCollapsed && (
                    <ul className="mh-diff-changes">
                      {sceneDiff.elements.map((change) => (
                        <ChangeRow
                          key={String(change.id)}
                          change={change}
                          ctx={authorCtx}
                          jumpable={Boolean(onJumpTo) && sceneIndex.has(sid)}
                          onJump={() => jumpToChange(sid, change)}
                        />
                      ))}
                    </ul>
                  )}
                </section>
              );
            })}
          </>
        )}
      </div>
    </div>
  );
}
