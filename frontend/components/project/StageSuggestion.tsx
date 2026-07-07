/**
 * StageSuggestion — the single most-relevant "next step" for the current SOP
 * stage (Phase B B3). A guided suggestion card inside the workbench: one
 * stage-aware line (specialized by the project's script count where it helps)
 * plus one navigation CTA to the tool that carries out that step.
 *
 * Deliberately NOT a second tool grid (that's B2's recommended-tools row) —
 * this is the narrative "do this next" nudge. The CTA navigates rather than
 * firing an AI action directly: generation lives inside the script editor,
 * not behind a project-level one-click endpoint.
 *
 * Flag-gated by VITE_FEATURE_PROJECT_AI_SUGGEST; the caller renders nothing
 * when off. Renders nothing for an unknown stage slug (degrades cleanly).
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb, ArrowRight } from 'lucide-react';
import { fetchScriptProjects } from '../../services/scriptService';
import type { ProjectStage, ProjectTab } from '../../types';

interface StageSuggestionProps {
  projectId: string;
  currentStage: ProjectStage | null;
  setActiveTab: (tab: ProjectTab) => void;
}

// Per-stage suggestion: which tab the CTA opens and which i18n CTA label.
// The message key is derived from the slug; `script` additionally swaps to
// `scriptEmpty` when the project has no scripts yet.
const STAGE_CTA: Record<string, { tab: ProjectTab; labelKey: string }> = {
  planning: { tab: 'scripts', labelKey: 'projects.suggest.ctaScripts' },
  script: { tab: 'scripts', labelKey: 'projects.suggest.ctaScripts' },
  storyboard: { tab: 'scripts', labelKey: 'projects.suggest.ctaScripts' },
  generation: { tab: 'output', labelKey: 'projects.suggest.ctaOutput' },
  review: { tab: 'files', labelKey: 'projects.suggest.ctaFiles' },
  delivery: { tab: 'output', labelKey: 'projects.suggest.ctaOutput' },
};

export function StageSuggestion({
  projectId,
  currentStage,
  setActiveTab,
}: StageSuggestionProps) {
  const { t } = useTranslation();
  const [scriptCount, setScriptCount] = useState<number | null>(null);

  const slug = currentStage?.slug ?? '';
  const needsCount = slug === 'script';

  useEffect(() => {
    if (!needsCount) {
      setScriptCount(null);
      return;
    }
    let cancelled = false;
    fetchScriptProjects(projectId, 1, 1)
      .then((res) => {
        if (!cancelled) setScriptCount(res.total);
      })
      .catch((err) => {
        console.error('[StageSuggestion] failed to load script count:', err);
        if (!cancelled) setScriptCount(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, needsCount]);

  const cta = STAGE_CTA[slug];
  if (!currentStage || !cta) return null;

  // `script` stage speaks to whether any scripts exist yet.
  let message: string;
  if (slug === 'script') {
    message =
      scriptCount && scriptCount > 0
        ? t('projects.suggest.script', { count: scriptCount })
        : t('projects.suggest.scriptEmpty');
  } else {
    message = t(`projects.suggest.${slug}`);
  }

  return (
    <div
      className="rounded-xl border border-indigo-500/35 bg-gradient-to-b from-indigo-500/[0.08] to-transparent p-4
                 flex items-start gap-3"
      data-testid="stage-suggestion"
    >
      <span className="grid place-items-center w-7 h-7 rounded-lg bg-indigo-500/15 text-indigo-400 shrink-0 mt-0.5">
        <Lightbulb className="w-3.5 h-3.5" />
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] uppercase tracking-wider text-indigo-400 font-semibold">
          {t('projects.suggest.eyebrow')}
        </div>
        <p className="text-sm text-ink-200 mt-1">{message}</p>
      </div>
      <button
        onClick={() => setActiveTab(cta.tab)}
        className="flex items-center gap-1.5 shrink-0 rounded-lg border border-indigo-500/40 hover:bg-indigo-500/15
                   text-indigo-300 font-medium text-sm px-3 py-1.5 transition-colors"
      >
        {t(cta.labelKey)}
        <ArrowRight size={14} />
      </button>
    </div>
  );
}

export default StageSuggestion;
