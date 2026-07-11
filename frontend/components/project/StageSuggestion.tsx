/**
 * StageSuggestion — the single most-relevant "next step" for the current SOP
 * stage (Phase B B3, redone). A data-aware guided card inside the workbench:
 * the backend's `/stage-suggestion` endpoint returns a `kind` (drives the
 * message copy + interpolation from `progress`) and an `action` (either
 * `navigate` — switch tabs — or `generate_missing_frames` — fire the
 * storyboard one-click batch directly from the card).
 *
 * Deliberately NOT a second tool grid (that's B2's recommended-tools row) —
 * this is the narrative "do this next" nudge, now backed by real progress
 * data instead of a static per-stage table.
 *
 * Renders nothing when the payload has no kind/action (unknown stage, or
 * project has nothing left to suggest).
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb, ArrowRight, Loader2 } from 'lucide-react';
import { fetchStageSuggestion, generateMissingFrames } from '../../services/projectsService';
import { useToast } from '../Toast';
import type { ProjectStage, ProjectTab, StageSuggestion as Suggestion } from '../../types';

interface StageSuggestionProps {
  projectId: string;
  currentStage: ProjectStage | null;
  setActiveTab: (tab: ProjectTab) => void;
}

export function StageSuggestion({
  projectId,
  currentStage,
  setActiveTab,
}: StageSuggestionProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [data, setData] = useState<Suggestion | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    let cancelled = false;
    fetchStageSuggestion(projectId)
      .then((s) => {
        if (!cancelled) setData(s);
      })
      .catch((err) => {
        console.error('[StageSuggestion] failed to load suggestion:', err);
        if (!cancelled) setData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, currentStage?.slug]);

  useEffect(() => load(), [load]);

  const onGenerate = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await generateMissingFrames(projectId);
      addToast(t('projects.suggest.generating', { count: res.dispatched_count }), 'success');
      load();
    } catch (err) {
      console.error('[StageSuggestion] generate-missing failed:', err);
      addToast(t('common.error'), 'error');
    } finally {
      setBusy(false);
    }
  }, [busy, projectId, addToast, t, load]);

  if (!data || !data.kind || !data.action) return null;

  const progress = data.progress ?? undefined;
  const message = t(`projects.suggest.${data.kind}`, {
    done: progress?.done,
    total: progress?.total,
    count: data.action.count,
    scene_count: progress?.scene_count,
  });
  const isGenerate = data.action.type === 'generate_missing_frames';

  return (
    <div
      className="rounded-xl border border-[var(--accent-border)] bg-island p-4
                 flex items-start gap-3"
      data-testid="stage-suggestion"
    >
      <span className="grid place-items-center w-7 h-7 rounded-lg bg-[var(--accent-soft)] text-[var(--accent-text)] shrink-0 mt-0.5">
        <Lightbulb className="w-3.5 h-3.5" />
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] uppercase tracking-wider text-[var(--accent-text)] font-semibold">
          {t('projects.suggest.eyebrow')}
        </div>
        <p className="text-sm text-ink-200 mt-1">{message}</p>
      </div>
      <button
        data-testid="suggest-cta"
        disabled={busy}
        onClick={() => (isGenerate ? onGenerate() : setActiveTab(data.action!.tab as ProjectTab))}
        className="flex items-center gap-1.5 shrink-0 rounded-lg font-medium text-sm px-3 py-1.5 transition-colors
                   bg-indigo-500 hover:bg-indigo-400 disabled:opacity-50 text-white"
      >
        {busy ? <Loader2 size={14} className="animate-spin" /> : null}
        {t(data.action.label_key, { count: data.action.count })}
        {!isGenerate && <ArrowRight size={14} />}
      </button>
    </div>
  );
}

export default StageSuggestion;
