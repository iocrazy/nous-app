// frontend/components/AILibrary/SkillEditor.tsx
// Skill editor — multi-file tabs (SKILL.md + skill.files[]) with add/delete.
//
// - System preset skills (is_public && !project_id) are read-only.
// - Drafts are held locally per-tab; Save commits all changes via API.
// - Add file prompts for a path, seeds empty markdown, switches to that tab.
// - Delete file confirms, removes, then falls back to SKILL.md.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibrarySkill } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { MarkdownEditor } from './MarkdownEditor';

interface SkillEditorProps {
  slug: string;
  onBack: () => void;
}

const SKILL_MD = 'SKILL.md';

export const SkillEditor: React.FC<SkillEditorProps> = ({ slug, onBack }) => {
  const { t } = useTranslation();
  const [skill, setSkill] = useState<AILibrarySkill | null>(null);
  const [activeTab, setActiveTab] = useState<string>(SKILL_MD);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (): Promise<void> => {
    try {
      const s = await aiLibraryService.getSkill(slug);
      setSkill(s);
      const nextDrafts: Record<string, string> = { [SKILL_MD]: s.body_md ?? '' };
      s.files.forEach((f) => {
        nextDrafts[f.path] = f.content ?? '';
      });
      setDrafts(nextDrafts);
    } catch (err) {
      console.error('[SkillEditor] getSkill failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    setSkill(null);
    setError(null);
    setActiveTab(SKILL_MD);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  if (error) {
    return (
      <div className="p-6">
        <button
          onClick={onBack}
          className="mb-3 text-sm text-zinc-400 hover:text-zinc-100"
        >
          ← Back
        </button>
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          Failed to load skill: {error}
        </div>
      </div>
    );
  }

  if (!skill) {
    return <div className="p-6 text-sm text-zinc-500">Loading...</div>;
  }

  // System preset skills in Phase 1 = is_public=true AND project_id is null.
  const isPreset = skill.is_public && !skill.project_id;

  const save = async (): Promise<void> => {
    if (isPreset) return;
    setSaving(true);
    setError(null);
    try {
      // Save SKILL.md body if changed.
      if (drafts[SKILL_MD] !== (skill.body_md ?? '')) {
        await aiLibraryService.updateSkill(slug, { body_md: drafts[SKILL_MD] });
      }
      // Save file edits (existing files only — new files are created via addFile).
      for (const f of skill.files) {
        const draft = drafts[f.path];
        if (draft !== undefined && draft !== (f.content ?? '')) {
          await aiLibraryService.upsertSkillFile(slug, f.path, draft, f.file_type);
        }
      }
      await load();
    } catch (err) {
      console.error('[SkillEditor] save failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const addFile = async (): Promise<void> => {
    if (isPreset) return;
    const input = window.prompt(
      t('aiLibrary.skills.pathPlaceholder') ?? 'path',
      'references/notes.md',
    );
    const path = (input ?? '').trim();
    if (!path) return;
    try {
      await aiLibraryService.upsertSkillFile(slug, path, '', 'markdown');
      await load();
      setActiveTab(path);
    } catch (err) {
      console.error('[SkillEditor] addFile failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const deleteFile = async (path: string): Promise<void> => {
    if (isPreset) return;
    if (path === SKILL_MD) return;
    const confirmLabel = t('aiLibrary.skills.deleteFile') ?? 'Delete file';
    if (!window.confirm(`${confirmLabel}: ${path}?`)) return;
    try {
      await aiLibraryService.deleteSkillFile(slug, path);
      await load();
      setActiveTab(SKILL_MD);
    } catch (err) {
      console.error('[SkillEditor] deleteFile failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const allTabs = [SKILL_MD, ...skill.files.map((f) => f.path)];
  const activeContent = drafts[activeTab] ?? '';
  const updateActive = (v: string): void =>
    setDrafts((d) => ({ ...d, [activeTab]: v }));

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-zinc-800 px-6 py-3">
        <button
          onClick={onBack}
          className="text-sm text-zinc-400 hover:text-zinc-100 transition-colors"
        >
          ← Back
        </button>
        <div className="min-w-0 flex-1 flex items-center justify-center gap-2">
          <h2 className="min-w-0 truncate font-semibold text-zinc-100">
            {skill.icon ? `${skill.icon} ` : ''}
            {skill.name}
          </h2>
          <SkillScopeBadge skill={skill} isPreset={isPreset} />
        </div>
        <button
          onClick={save}
          disabled={saving || isPreset}
          className="rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-4 py-1.5 text-sm font-medium text-indigo-400 hover:bg-indigo-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
        >
          {saving ? 'Saving...' : t('aiLibrary.agents.saveChanges')}
        </button>
      </header>

      <div className="grid flex-1 grid-cols-[260px_1fr] overflow-hidden">
        <aside className="overflow-y-auto border-r border-zinc-800 bg-zinc-950/40 p-4 text-sm">
          <dl className="grid grid-cols-1 gap-y-3">
            <div>
              <dt className="text-xs text-zinc-500">Name</dt>
              <dd className="mt-0.5 text-zinc-200">{skill.name}</dd>
            </div>
            {skill.description && (
              <div>
                <dt className="text-xs text-zinc-500">Description</dt>
                <dd className="mt-0.5 text-zinc-300">{skill.description}</dd>
              </div>
            )}
            {skill.category && (
              <div>
                <dt className="text-xs text-zinc-500">Category</dt>
                <dd className="mt-0.5 text-zinc-300">{skill.category}</dd>
              </div>
            )}
            <div>
              <dt className="text-xs text-zinc-500">Icon</dt>
              <dd className="mt-0.5 text-zinc-300">{skill.icon ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-xs text-zinc-500">Scope</dt>
              <dd className="mt-0.5 text-zinc-300">
                {isPreset
                  ? t('aiLibrary.agents.systemPreset')
                  : skill.project_id
                    ? 'Project'
                    : skill.team_id
                      ? 'Team'
                      : 'Personal'}
              </dd>
            </div>
            {skill.slug && (
              <div>
                <dt className="text-xs text-zinc-500">Slug</dt>
                <dd className="mt-0.5 font-mono text-xs text-zinc-400">{skill.slug}</dd>
              </div>
            )}
          </dl>
        </aside>

        <section className="flex flex-col overflow-hidden">
          <nav className="flex items-center gap-1 overflow-x-auto border-b border-zinc-800 bg-zinc-950/40 px-4 py-2">
            {allTabs.map((p) => {
              const active = activeTab === p;
              return (
                <div key={p} className="group relative flex items-center">
                  <button
                    onClick={() => setActiveTab(p)}
                    className={`whitespace-nowrap rounded px-3 py-1 text-sm transition-colors ${
                      active
                        ? 'bg-zinc-800 text-zinc-100'
                        : 'text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200'
                    }`}
                  >
                    {p}
                  </button>
                  {!isPreset && p !== SKILL_MD && (
                    <button
                      onClick={() => deleteFile(p)}
                      title={t('aiLibrary.skills.deleteFile') ?? 'Delete'}
                      className="ml-1 hidden rounded px-1 text-zinc-500 hover:bg-red-500/20 hover:text-red-300 group-hover:block"
                    >
                      ✕
                    </button>
                  )}
                </div>
              );
            })}
            {!isPreset && (
              <button
                onClick={addFile}
                className="ml-2 rounded px-3 py-1 text-sm text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200 transition-colors"
              >
                {t('aiLibrary.skills.addFile')}
              </button>
            )}
          </nav>

          <div className="flex-1 overflow-auto p-6">
            {isPreset && (
              <div className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                {t('aiLibrary.agents.presetReadOnly')}
              </div>
            )}
            <MarkdownEditor
              value={activeContent}
              onChange={updateActive}
              disabled={isPreset}
              rows={24}
            />
          </div>
        </section>
      </div>
    </div>
  );
};

/**
 * Tiny presentational badge showing the skill's scope: system preset,
 * team-scoped, project-scoped, or private. Mirrors ScopeBadge from
 * AgentEditor.tsx — kept inline here to avoid cross-file imports while
 * the pattern is still settling.
 */
const SkillScopeBadge: React.FC<{
  skill: AILibrarySkill;
  isPreset: boolean;
}> = ({ skill, isPreset }) => {
  const { t } = useTranslation();
  const base = 'ml-1 rounded border px-2 py-0.5 text-xs whitespace-nowrap';

  if (isPreset) {
    return (
      <span className={`${base} border-zinc-700 bg-zinc-800 text-zinc-300`}>
        {t('aiLibrary.agents.systemPreset', 'System Preset')}
      </span>
    );
  }
  if (skill.team_id != null) {
    return (
      <span className={`${base} border-indigo-500/40 bg-indigo-500/10 text-indigo-300`}>
        {t('aiLibrary.skills.scopeBadgeTeam', 'Team: {{name}}', {
          name: skill.team_name ?? skill.team_id,
        })}
      </span>
    );
  }
  if (skill.project_id != null) {
    return (
      <span className={`${base} border-emerald-500/40 bg-emerald-500/10 text-emerald-300`}>
        {t('aiLibrary.skills.scopeBadgeProject', 'Project: {{name}}', {
          name: skill.project_name ?? skill.project_id,
        })}
      </span>
    );
  }
  return (
    <span className={`${base} border-zinc-700 bg-zinc-900 text-zinc-400`}>
      {t('aiLibrary.skills.scopeBadgePrivate', 'Private')}
    </span>
  );
};

export default SkillEditor;
