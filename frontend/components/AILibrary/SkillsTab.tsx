// frontend/components/AILibrary/SkillsTab.tsx
// Left rail: list of skills (system presets + custom) + "New Skill" button,
// grouped by scope (System Presets → My Private → each Team → each Project).
// Right pane: SkillEditor for the selected skill.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibrarySkill } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { SkillEditor } from './SkillEditor';
import { NewSkillModal } from './NewSkillModal';
import { ScopedList, groupByScope } from './ScopedList';
import type { ScopedGroup, ScopedLabels } from './ScopedList';

/** Preset detector for skills — public + no team/project ownership. */
const skillIsSystemPreset = (s: AILibrarySkill): boolean =>
  s.is_public && s.team_id == null && s.project_id == null;

/**
 * Thin wrapper around the shared ``groupByScope`` helper so unit tests can
 * keep exercising the skill-specific groupings via a stable import.
 */
export function groupSkillsByScope(
  skills: AILibrarySkill[],
  labels: ScopedLabels,
): ScopedGroup<AILibrarySkill>[] {
  return groupByScope(skills, skillIsSystemPreset, labels);
}

interface SkillsTabProps {
  /** Controlled selected slug (e.g., from URL). If omitted, falls back to internal state. */
  slug?: string | null;
  /** Called when user picks a skill. When provided, parent controls selection. */
  onSlugChange?: (slug: string) => void;
}

export const SkillsTab: React.FC<SkillsTabProps> = ({ slug, onSlugChange }) => {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<AILibrarySkill[]>([]);
  const [internalSlug, setInternalSlug] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showNewSkillModal, setShowNewSkillModal] = useState(false);

  const isControlled = slug !== undefined;
  const selectedSlug = isControlled ? (slug ?? null) : internalSlug;
  const setSelectedSlug = (next: string | null) => {
    if (next && onSlugChange) onSlugChange(next);
    if (!isControlled) setInternalSlug(next);
  };

  const loadSkills = React.useCallback(async () => {
    try {
      setLoading(true);
      const list = await aiLibraryService.listSkills();
      setSkills(list);
    } catch (err) {
      console.error('[SkillsTab] listSkills failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await aiLibraryService.listSkills();
        if (cancelled) return;
        setSkills(list);
      } catch (err) {
        if (cancelled) return;
        console.error('[SkillsTab] listSkills failed:', err);
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCreated = async (newSlug: string) => {
    setShowNewSkillModal(false);
    setSelectedSlug(newSlug);
    await loadSkills();
  };

  if (loading) {
    return <div className="p-6 text-sm text-zinc-500">Loading skills...</div>;
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          Failed to load skills: {error}
        </div>
      </div>
    );
  }

  // If an editor is open, show it full-width with a Back button.
  if (selectedSlug) {
    return (
      <SkillEditor
        slug={selectedSlug}
        onBack={() => {
          setSelectedSlug(null);
          void loadSkills();
        }}
        onSkillForked={async (newSlug) => {
          await loadSkills();
          setSelectedSlug(newSlug);
        }}
      />
    );
  }

  const labels: ScopedLabels = {
    systemPresets: t('aiLibrary.skills.groupSystemPresets', 'System Presets'),
    privateLabel: t('aiLibrary.skills.groupPrivate', 'My Private'),
    teamLabel: (name: string) =>
      t('aiLibrary.skills.groupTeam', 'Team: {{name}}', { name }),
    projectLabel: (name: string) =>
      t('aiLibrary.skills.groupProject', 'Project: {{name}}', { name }),
  };
  const hasSkills = skills.length > 0;

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-200">
          {t('aiLibrary.skills.listTitle', 'Skills')}
        </h3>
        <button
          type="button"
          onClick={() => setShowNewSkillModal(true)}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500"
        >
          + {t('aiLibrary.skills.newSkillButton', 'New Skill')}
        </button>
      </div>

      {!hasSkills ? (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-6 text-sm text-zinc-500">
          {t('aiLibrary.skills.empty', 'No skills yet. Create your first one.')}
        </div>
      ) : (
        <div className="space-y-6">
          <ScopedList
            items={skills}
            isSystemPreset={skillIsSystemPreset}
            labels={labels}
            headerVariant="section"
            renderGroupBody={(_group, children) => (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {children}
              </div>
            )}
            renderItem={(s) => (
              <button
                onClick={() => setSelectedSlug(s.slug ?? String(s.id))}
                className="rounded-xl border border-zinc-800 bg-zinc-900 p-4 text-left transition-colors hover:border-zinc-700 hover:bg-zinc-800/60 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <div className="mb-2 text-2xl leading-none">{s.icon ?? '🧩'}</div>
                <div className="truncate font-semibold text-zinc-100">{s.name}</div>
                {s.category && (
                  <div className="mt-1 truncate text-xs text-zinc-500">
                    {s.category}
                  </div>
                )}
                {s.description && (
                  <p className="mt-2 line-clamp-3 text-sm text-zinc-400">
                    {s.description}
                  </p>
                )}
              </button>
            )}
          />
        </div>
      )}

      {showNewSkillModal && (
        <NewSkillModal
          existingSkills={skills}
          onClose={() => setShowNewSkillModal(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
};

export default SkillsTab;
