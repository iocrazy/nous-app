/**
 * VersionDiff — the centre-pane comparison surface (Phase B P4). When the writer
 * hits Compare on a version, the shell swaps the paper column for this view: it
 * replays the op ledger on the server (via `diffCommit`) and renders the result
 * as a per-scene change stream — added / removed / changed / moved elements, with
 * changed elements showing their before (struck red) and after (highlit green)
 * text. Scene-set adds/removes render as their own badges above the element list.
 *
 * The header names the compared commit against the live 'Current' state (the
 * only comparison the panel offers today) and carries a Back button that clears
 * the shell's diff state. Ids are strings end-to-end; scene-heading lookup
 * coerces both sides with String() (#1006). Dual-theme via the shell CSS vars;
 * zero emoji.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  diffCommit,
  type CommitDiff,
  type DiffChange,
  type ScriptCommit,
  type SceneSnapshot,
} from '../sceneService';
import type { SceneDoc } from '../types';

export interface VersionDiffProps {
  scriptId: string;
  commit: ScriptCommit;
  /** Commit id to diff against, or 'current' for the live state (default). */
  against?: string;
  /** Display label for the right side of the comparison (default: Current). */
  againstLabel?: string;
  /** Live scenes — the source for a changed scene's heading in the diff. */
  scenes: SceneDoc[];
  onBack: () => void;
}

type LoadState = 'loading' | 'ready' | 'error';

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

export function VersionDiff({
  scriptId,
  commit,
  against = 'current',
  againstLabel,
  scenes,
  onBack,
}: VersionDiffProps) {
  const { t } = useTranslation();
  const [diff, setDiff] = useState<CommitDiff | null>(null);
  const [loadState, setLoadState] = useState<LoadState>('loading');

  const headingFor = useCallback(
    (sceneId: string): string => {
      const scene = scenes.find((s) => String(s.id) === String(sceneId));
      const line = scene ? sceneHeading(scene) : '';
      return line || t('editor.untitledScene');
    },
    [scenes, t],
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
            {t('editor.diffLoading')}
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
            {(diff.scenes_added.length > 0 || diff.scenes_removed.length > 0) && (
              <section className="mh-diff-scene-set" aria-label={t('editor.diffSceneSet')}>
                {diff.scenes_added.map((snap) => (
                  <div
                    key={`add-${String(snap.id)}`}
                    className="mh-diff-scene-chip added"
                    data-testid="diff-scene-added"
                  >
                    <span className="mh-diff-badge added">{t('editor.diffSceneAdded')}</span>
                    <span className="mh-diff-scene-name">
                      {snapshotHeading(snap) || t('editor.untitledScene')}
                    </span>
                  </div>
                ))}
                {diff.scenes_removed.map((snap) => (
                  <div
                    key={`rm-${String(snap.id)}`}
                    className="mh-diff-scene-chip removed"
                    data-testid="diff-scene-removed"
                  >
                    <span className="mh-diff-badge removed">{t('editor.diffSceneRemoved')}</span>
                    <span className="mh-diff-scene-name">
                      {snapshotHeading(snap) || t('editor.untitledScene')}
                    </span>
                  </div>
                ))}
              </section>
            )}

            {diff.scenes.map((sceneDiff) => (
              <section
                key={String(sceneDiff.scene_id)}
                className="mh-diff-scene"
                data-testid="diff-scene"
              >
                <h3 className="mh-diff-scene-heading">{headingFor(sceneDiff.scene_id)}</h3>
                <ul className="mh-diff-changes">
                  {sceneDiff.elements.map((change) => (
                    <li
                      key={String(change.id)}
                      className={`mh-diff-change ${change.kind}`}
                      data-testid="diff-change"
                      data-kind={change.kind}
                    >
                      <span className={`mh-diff-badge ${change.kind}`}>
                        {t(KIND_LABEL[change.kind])}
                      </span>
                      <div className="mh-diff-texts">
                        {(change.kind === 'changed' || change.kind === 'removed') &&
                          change.before && (
                            <div className="mh-diff-text before" data-testid="diff-before">
                              {change.before.text || t('editor.diffEmptyLine')}
                            </div>
                          )}
                        {(change.kind === 'changed' ||
                          change.kind === 'added' ||
                          change.kind === 'moved') &&
                          change.after && (
                            <div className="mh-diff-text after" data-testid="diff-after">
                              {change.after.text || t('editor.diffEmptyLine')}
                            </div>
                          )}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
