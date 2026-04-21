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

/**
 * One rendered section in the sidebar. ``key`` doubles as the React key and
 * a stable identifier for test selectors; it is NOT shown to the user.
 */
interface SkillGroup {
  key: string;
  label: string;
  skills: AILibrarySkill[];
}

/**
 * Slot a skill into a scope-derived group. Ordering priority:
 *   1. System Presets (is_public && !team_id && !project_id)
 *   2. My Private     (user-owned, no team / no project)
 *   3. Each Team      (one group per distinct team_id)
 *   4. Each Project   (one group per distinct project_id)
 *
 * Exported separately so it can be unit-tested without a React render.
 */
export function groupSkillsByScope(
  skills: AILibrarySkill[],
  labels: {
    systemPresets: string;
    privateLabel: string;
    teamLabel: (name: string) => string;
    projectLabel: (name: string) => string;
  },
): SkillGroup[] {
  const presets: AILibrarySkill[] = [];
  const privateOnes: AILibrarySkill[] = [];
  const byTeam = new Map<number, { name: string; skills: AILibrarySkill[] }>();
  const byProject = new Map<number, { name: string; skills: AILibrarySkill[] }>();

  for (const s of skills) {
    const isSystemPreset =
      s.is_public && s.team_id == null && s.project_id == null;
    if (isSystemPreset) {
      presets.push(s);
      continue;
    }
    if (s.team_id != null) {
      const entry = byTeam.get(s.team_id) ?? {
        name: s.team_name ?? String(s.team_id),
        skills: [],
      };
      entry.skills.push(s);
      byTeam.set(s.team_id, entry);
      continue;
    }
    if (s.project_id != null) {
      const entry = byProject.get(s.project_id) ?? {
        name: s.project_name ?? String(s.project_id),
        skills: [],
      };
      entry.skills.push(s);
      byProject.set(s.project_id, entry);
      continue;
    }
    privateOnes.push(s);
  }

  const groups: SkillGroup[] = [];
  if (presets.length > 0) {
    groups.push({ key: 'system', label: labels.systemPresets, skills: presets });
  }
  if (privateOnes.length > 0) {
    groups.push({ key: 'private', label: labels.privateLabel, skills: privateOnes });
  }
  for (const [teamId, entry] of byTeam) {
    groups.push({
      key: `team:${teamId}`,
      label: labels.teamLabel(entry.name),
      skills: entry.skills,
    });
  }
  for (const [projectId, entry] of byProject) {
    groups.push({
      key: `project:${projectId}`,
      label: labels.projectLabel(entry.name),
      skills: entry.skills,
    });
  }
  return groups;
}

export const SkillsTab: React.FC = () => {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<AILibrarySkill[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showNewSkillModal, setShowNewSkillModal] = useState(false);

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
      />
    );
  }

  const groups = groupSkillsByScope(skills, {
    systemPresets: t('aiLibrary.skills.groupSystemPresets', 'System Presets'),
    privateLabel: t('aiLibrary.skills.groupPrivate', 'My Private'),
    teamLabel: (name: string) =>
      t('aiLibrary.skills.groupTeam', 'Team: {{name}}', { name }),
    projectLabel: (name: string) =>
      t('aiLibrary.skills.groupProject', 'Project: {{name}}', { name }),
  });

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

      {groups.length === 0 ? (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-6 text-sm text-zinc-500">
          {t('aiLibrary.skills.empty', 'No skills yet. Create your first one.')}
        </div>
      ) : (
        <div className="space-y-6">
          {groups.map((group) => (
            <section key={group.key}>
              <div className="sticky top-0 z-10 -mx-6 mb-3 border-b border-zinc-800/60 bg-zinc-950/95 px-6 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                {group.label}
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {group.skills.map((s) => {
                  const key = s.slug ?? String(s.id);
                  return (
                    <button
                      key={key}
                      onClick={() => setSelectedSlug(s.slug ?? String(s.id))}
                      className="rounded-xl border border-zinc-800 bg-zinc-900 p-4 text-left transition-colors hover:border-zinc-700 hover:bg-zinc-800/60 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    >
                      <div className="mb-2 text-2xl leading-none">
                        {s.icon ?? '🧩'}
                      </div>
                      <div className="truncate font-semibold text-zinc-100">
                        {s.name}
                      </div>
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
                  );
                })}
              </div>
            </section>
          ))}
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
