// frontend/components/AILibrary/SkillEditor.tsx
//
// Paperclip-style SkillPane — ported 1:1 from paperclip
// (ui/src/pages/CompanySkills.tsx::SkillPane) while keeping Nous's
// existing data layer (aiLibraryService + skill_files table).
//
// Layout:
//   header
//     row 1: H1 (SourceIcon + name)           Remove | Edit / Fork
//            description below name
//     row 2 (border-t, pt-4): metadata strip
//            SOURCE | KEY | MODE | USED BY
//     (preset banner if bundled)
//   sub-header (border-b, px-5 py-3)
//     left: current file path (font-mono)
//     right: View|Code toggle (preview) OR Cancel|Save (edit)
//   content (min-h[560], px-5 py-5)
//     edit + markdown   -> <MarkdownEditor>
//     edit + non-md     -> <textarea>
//     preview + markdown -> <MarkdownBody>
//     code / non-md     -> <pre><code>
//
// Differences from paperclip:
// - Nous has "Bundled preset" vs "User/Team/Project" scopes — no
//   GitHub / skills.sh / URL source variants (and thus no "Check for
//   updates" workflow).
// - "Used by" renders the real reverse index: GET /skills and
//   /skills/:slug both return ``agents`` (B3, spec 2026-08-02 §B3).

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';
import type { TFunction } from 'i18next';
import {
  Code2,
  Eye,
  GitFork,
  Package,
  Pencil,
  Save,
  Trash2,
  User as UserIcon,
  Users,
} from 'lucide-react';
import type { AILibrarySkill } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../Toast';
import { MarkdownBody } from './MarkdownBody';
import { MarkdownEditor } from './MarkdownEditor';
import { NewSkillModal } from './NewSkillModal';
import { VersionHistoryPanel } from './VersionHistoryPanel';

interface SkillEditorProps {
  slug: string;
  onBack: () => void;
  onSkillForked?: (newSlug: string) => void;
  /**
   * Called after a successful delete. Parent must refresh its skill list
   * AND clear the selected slug — ``onBack`` alone only clears the URL
   * slug, leaving the deleted skill as a stale row in the left rail that
   * 404s when clicked.
   */
  onSkillDeleted?: () => void;
  /** URL-driven active file. ``''`` or null = SKILL.md. */
  filePath?: string | null;
  /** V6 split-pane hides the in-editor Back button. */
  hideBack?: boolean;
  /**
   * Notify the parent that a different file tab was picked, so it can put
   * the path in the URL. Without it the tabs still work — they just fall
   * back to local state and the deep link stays on the previous file.
   */
  onSelectFile?: (path: string) => void;
  /** Open the parent's "new file" modal for this skill. */
  onNewFile?: () => void;
}

const SKILL_MD = 'SKILL.md';

/**
 * Strip a leading YAML frontmatter block so the rendered preview doesn't
 * dump name/description/etc above the heading. Matches paperclip's helper.
 */
function stripFrontmatter(md: string): string {
  const lines = md.split(/\r?\n/);
  if (lines[0] !== '---') return md;
  let end = -1;
  for (let i = 1; i < lines.length; i++) {
    if (lines[i] === '---') {
      end = i;
      break;
    }
  }
  if (end < 0) return md;
  return lines
    .slice(end + 1)
    .join('\n')
    .replace(/^\n+/, '');
}

/** Classify the skill source — bundled seed vs user-owned scope. */
/** `managedLabel` used to ride along here but nothing ever rendered it. */
function skillSource(skill: AILibrarySkill, t: TFunction) {
  const bundled =
    skill.is_public && skill.team_id == null && skill.project_id == null;
  if (bundled) {
    return { icon: Package, label: t('aiLibrary.skills.builtIn', 'Built-in') };
  }
  if (skill.team_id != null) {
    return {
      icon: Users,
      label: t('aiLibrary.agents.scopeBadgeTeam', 'Team: {{name}}', {
        name: skill.team_name ?? skill.team_id,
      }),
    };
  }
  if (skill.project_id != null) {
    return {
      icon: Users,
      label: t('aiLibrary.agents.scopeBadgeProject', 'Project: {{name}}', {
        name: skill.project_name ?? skill.project_id,
      }),
    };
  }
  return { icon: UserIcon, label: t('aiLibrary.scope.private', 'Private') };
}

export const SkillEditor: React.FC<SkillEditorProps> = ({
  slug,
  onBack,
  onSkillForked,
  onSkillDeleted,
  filePath = null,
  hideBack = false,
  onSelectFile,
  onNewFile,
}) => {
  const { t } = useTranslation();
  const { userProfile } = useAuth();
  const { addToast } = useToast();
  const { teamId } = useParams();
  const urlPrefix = teamId ? `/team/${teamId}` : '';
  // ``userProfile.role === 'admin'`` no longer gates the delete button —
  // backend rejects DELETE on bundled skills for everyone in Phase 1
  // (same shape as the agent preset read-only rule fixed in PR #309).
  // Keeping the destructure so callers down-tree don't break.
  void userProfile;

  const [skill, setSkill] = useState<AILibrarySkill | null>(null);
  const [activeTab, setActiveTab] = useState<string>(SKILL_MD);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forkModalOpen, setForkModalOpen] = useState(false);
  const [allSkills, setAllSkills] = useState<AILibrarySkill[]>([]);
  const [viewMode, setViewMode] = useState<'preview' | 'code'>('preview');
  const [editMode, setEditMode] = useState(false);

  const load = async (): Promise<void> => {
    try {
      const s = await aiLibraryService.getSkill(slug);
      setSkill(s);
      const nextDrafts: Record<string, string> = {
        [SKILL_MD]: s.body_md ?? '',
      };
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
    setEditMode(false);
    setViewMode('preview');
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  useEffect(() => {
    if (filePath == null) return;
    setActiveTab(filePath === '' ? SKILL_MD : filePath);
    setEditMode(false);
  }, [filePath]);

  const activeFile = useMemo(() => {
    if (!skill) return null;
    if (activeTab === SKILL_MD) {
      return {
        path: SKILL_MD,
        file_type: 'markdown' as const,
        content: skill.body_md ?? '',
      };
    }
    return skill.files.find((f) => f.path === activeTab) ?? null;
  }, [skill, activeTab]);

  const isMarkdown =
    activeFile?.file_type === 'markdown' || activeTab.endsWith('.md');

  if (error) {
    return (
      <div className="p-6">
        {!hideBack && (
          <button
            onClick={onBack}
            className="mb-3 text-sm text-ink-400 hover:text-ink-100"
          >
            ← {t('aiLibrary.skills.back')}
          </button>
        )}
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          {t('aiLibrary.skills.loadErrorPrefix')}: {error}
        </div>
      </div>
    );
  }

  if (!skill) {
    return <div className="p-6 text-sm text-ink-500">{t('common.loading')}</div>;
  }

  const isPreset =
    skill.is_public && skill.team_id == null && skill.project_id == null;
  const editable = !isPreset;
  const editableReason = isPreset
    ? t(
        'aiLibrary.skills.bundledReadOnlyHint',
        'Bundled Nous skills are read-only. Fork to edit.',
      )
    : '';
  // Bundled (preset) skills are read-only for everyone in Phase 1; the
  // backend DELETE route returns 403. UI matches so we don't lure the
  // user into a confirm dialog that ends with a raw 403.
  const canDelete = !isPreset;
  const source = skillSource(skill, t);
  const SourceIcon = source.icon;

  const activeDraft = drafts[activeTab] ?? '';
  const activeContent = activeFile?.content ?? '';
  const activeBody = isMarkdown
    ? activeTab === SKILL_MD
      ? stripFrontmatter(activeContent)
      : activeContent
    : activeContent;

  const updateActive = (v: string): void =>
    setDrafts((d) => ({ ...d, [activeTab]: v }));

  const save = async (): Promise<void> => {
    if (!editable) return;
    setSaving(true);
    setError(null);
    try {
      if (activeTab === SKILL_MD) {
        if (drafts[SKILL_MD] !== (skill.body_md ?? '')) {
          await aiLibraryService.updateSkill(slug, {
            body_md: drafts[SKILL_MD],
          });
        }
      } else {
        const f = skill.files.find((x) => x.path === activeTab);
        if (f && drafts[activeTab] !== (f.content ?? '')) {
          await aiLibraryService.upsertSkillFile(
            slug,
            activeTab,
            drafts[activeTab],
            f.file_type,
          );
        }
      }
      await load();
      setEditMode(false);
      addToast(t('aiLibrary.skills.savedToast', 'Saved'), 'success');
    } catch (err) {
      console.error('[SkillEditor] save failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      addToast(msg, 'error');
    } finally {
      setSaving(false);
    }
  };

  const openForkModal = async (): Promise<void> => {
    setForkModalOpen(true);
    if (allSkills.length === 0) {
      try {
        const list = await aiLibraryService.listSkills();
        setAllSkills(list);
      } catch (err) {
        console.error('[SkillEditor] listSkills for fork failed:', err);
      }
    }
  };

  const handleForkCreated = (newSlug: string): void => {
    setForkModalOpen(false);
    addToast(
      t('aiLibrary.skills.forkedToast', 'Forked as {{slug}}', {
        slug: newSlug,
      }),
      'success',
    );
    if (onSkillForked) onSkillForked(newSlug);
    else onBack();
  };

  const handleDelete = async (): Promise<void> => {
    if (!canDelete) return;
    const prompt = t(
      'aiLibrary.skills.deleteSkillConfirm',
      'Remove skill "{{name}}"? This cannot be undone.',
      { name: skill.name },
    );
    if (!window.confirm(prompt)) return;
    setDeleting(true);
    setError(null);
    try {
      await aiLibraryService.deleteSkill(slug);
      addToast(
        t('aiLibrary.skills.deletedToast', 'Removed: {{name}}', {
          name: skill.name,
        }),
        'success',
      );
      // Prefer onSkillDeleted (refreshes the rail) over onBack (URL-only),
      // so the just-deleted skill doesn't linger as a clickable stale row.
      if (onSkillDeleted) onSkillDeleted();
      else onBack();
    } catch (err) {
      console.error('[SkillEditor] deleteSkill failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      addToast(msg, 'error');
    } finally {
      setDeleting(false);
    }
  };

  const fileCount = 1 + skill.files.length;

  return (
    <div className="flex h-full min-w-0 flex-col overflow-y-auto">
      {/* ── Header ────────────────────────────────────────────────── */}
      <div className="px-5 pt-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-ok-soft text-ok">
              <SourceIcon className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <h1 className="flex items-center gap-2 text-lg font-semibold text-ink-100">
                <span className="truncate">{skill.name}</span>
                {!editable && (
                  <span className="shrink-0 rounded border border-info-line bg-info-soft px-1.5 py-0.5 text-[10.5px] font-normal text-info">
                    {t('aiLibrary.skills.builtInReadOnly', 'Built-in · read only')}
                  </span>
                )}
              </h1>
              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-ink-500">
                <span className="font-mono">{skill.slug ?? String(skill.id)}</span>
                <span>·</span>
                <span>
                  {t('aiLibrary.skills.fileCount', '{{count}} files', { count: fileCount })}
                </span>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {canDelete && (
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting || saving}
                className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-ink-400 transition-colors hover:bg-ink-800/60 hover:text-ink-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Trash2 className="h-3.5 w-3.5" />
                {deleting
                  ? t('common.removing', 'Removing...')
                  : t('aiLibrary.skills.remove', 'Remove')}
              </button>
            )}
            {editable ? (
              <button
                type="button"
                onClick={() => setEditMode((v) => !v)}
                className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-ink-400 transition-colors hover:bg-ink-800/60 hover:text-ink-100"
              >
                <Pencil className="h-3.5 w-3.5" />
                {editMode
                  ? t('aiLibrary.skills.stopEditing', 'Stop editing')
                  : t('aiLibrary.skills.edit', 'Edit')}
              </button>
            ) : (
              <button
                type="button"
                onClick={openForkModal}
                className="inline-flex items-center gap-1.5 rounded-md bg-[var(--accent-soft)] px-2.5 py-1.5 text-sm text-[var(--accent-text)] transition-colors hover:bg-[var(--accent-soft)]"
                title={editableReason}
              >
                <GitFork className="h-3.5 w-3.5" />
                {t('aiLibrary.skills.forkToEdit', 'Fork to edit')}
              </button>
            )}
          </div>
        </div>

        {skill.description && (
          <p className="mt-3 max-w-3xl text-sm text-ink-400">{skill.description}</p>
        )}

        {!editable && (
          <div className="mt-3 rounded border border-warn-line bg-warn-soft px-3 py-2 text-[12px] text-warn">
            {editableReason}
          </div>
        )}
      </div>

      {/* ── Body: 1.6fr document / 1fr side panels (spec §06) ─────── */}
      <div className="grid min-h-0 flex-1 grid-cols-1 items-start gap-4 px-5 py-4 md:grid-cols-[1.6fr_1fr]">
      <div className="min-w-0 rounded-lg border border-ink-800">
      {/* ── Sub-header: file tabs + toggle ────────────────────────── */}
      <div className="border-b border-ink-800/80 px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* File tabs carry navigation between a skill's files now — the
              left rail's per-skill tree did it before, which meant the
              editor never showed what else was in the skill you had open. */}
          <div
            className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto"
            data-testid="skill-file-tabs"
          >
            {[SKILL_MD, ...skill.files.map((f) => f.path)].map((path) => (
              <button
                key={path}
                type="button"
                onClick={() => {
                  setActiveTab(path);
                  onSelectFile?.(path === SKILL_MD ? '' : path);
                }}
                data-testid="skill-file-tab"
                aria-pressed={activeTab === path}
                className={`shrink-0 rounded-t border-b-2 px-2.5 py-1 font-mono text-xs transition-colors ${
                  activeTab === path
                    ? 'border-[var(--accent-border)] text-ink-100'
                    : 'border-transparent text-ink-500 hover:text-ink-300'
                }`}
              >
                {path}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            {/* Deliberately OUTSIDE the scroller above: in it, a skill with a
                few files pushed this off the right edge with no affordance
                that it was there. On a built-in skill it stays visible but
                disabled — hiding it read as "this feature doesn't exist"
                rather than "fork first", which is what got reported. */}
            {onNewFile && (
              <button
                type="button"
                onClick={onNewFile}
                disabled={!editable}
                data-testid="skill-new-file"
                title={
                  editable
                    ? undefined
                    : t(
                        'aiLibrary.skills.newFileLockedHint',
                        'Fork this skill to add files',
                      )
                }
                className="shrink-0 rounded px-2 py-1 text-xs text-ok hover:text-ok disabled:cursor-not-allowed disabled:text-ink-600"
              >
                {t('aiLibrary.skills.newFile', '+ File')}
              </button>
            )}
            {editMode && editable ? (
              <>
                <button
                  type="button"
                  onClick={() => {
                    setEditMode(false);
                    setDrafts((d) => ({ ...d, [activeTab]: activeContent }));
                  }}
                  disabled={saving}
                  className="rounded-md px-2.5 py-1.5 text-sm text-ink-400 hover:bg-ink-800/60 hover:text-ink-100 disabled:opacity-50"
                >
                  {t('common.cancel', 'Cancel')}
                </button>
                <button
                  type="button"
                  onClick={save}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 rounded-md btn-tint-indigo px-3 py-1.5 text-sm font-medium disabled:opacity-50"
                >
                  <Save className="h-3.5 w-3.5" />
                  {saving
                    ? t('common.saving', 'Saving...')
                    : t('common.save', 'Save')}
                </button>
              </>
            ) : isMarkdown ? (
              <div className="flex items-center overflow-hidden rounded border border-ink-800">
                <button
                  type="button"
                  onClick={() => setViewMode('preview')}
                  className={`flex items-center gap-1.5 px-3 py-1 text-sm transition-colors ${
                    viewMode === 'preview'
                      ? 'bg-indigo-600 text-white'
                      : 'text-ink-500 hover:text-ink-200'
                  }`}
                >
                  <Eye className="h-3.5 w-3.5" />
                  {t('aiLibrary.skills.viewTab', 'View')}
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode('code')}
                  className={`flex items-center gap-1.5 border-l border-ink-800 px-3 py-1 text-sm transition-colors ${
                    viewMode === 'code'
                      ? 'bg-indigo-600 text-white'
                      : 'text-ink-500 hover:text-ink-200'
                  }`}
                >
                  <Code2 className="h-3.5 w-3.5" />
                  {t('aiLibrary.skills.codeTab', 'Code')}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {/* ── Content ───────────────────────────────────────────────── */}
      <div className="px-4 py-4">
        {editMode && editable ? (
          isMarkdown ? (
            <MarkdownEditor
              value={activeDraft}
              onChange={updateActive}
              rows={24}
            />
          ) : (
            <textarea
              value={activeDraft}
              onChange={(e) => updateActive(e.target.value)}
              className="h-[560px] w-full border-0 bg-transparent p-0 font-mono text-sm text-ink-200 focus:outline-none"
              spellCheck={false}
            />
          )
        ) : isMarkdown && viewMode === 'preview' ? (
          <MarkdownBody source={activeBody} />
        ) : (
          <pre className="whitespace-pre-wrap break-words font-mono text-sm text-ink-200">
            <code>{activeContent}</code>
          </pre>
        )}
      </div>
      </div>

      {/* ── Right column: who uses this, and how it got here ──────── */}
      <div className="flex flex-col gap-4">
        <section className="rounded-lg border border-ink-800">
          <h2 className="px-4 pt-3 pb-2 text-[12px] font-semibold text-ink-400">
            {t('aiLibrary.skills.metaUsedBy', 'Used by')}
          </h2>
          {skill.agents && skill.agents.length > 0 ? (
            skill.agents.map((a) => (
              <div
                key={a.slug}
                data-testid="skill-used-by-row"
                className="flex items-center gap-2.5 border-t border-ink-800/60 px-4 py-2.5 text-[12.5px]"
              >
                <span className="grid h-5 w-5 shrink-0 place-items-center rounded bg-agent-soft text-[10px] font-bold text-agent">
                  {a.name.slice(0, 1).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1 truncate text-ink-200">{a.name}</span>
                <Link
                  to={`${urlPrefix}/ai-library/agents/${a.slug}`}
                  className="shrink-0 text-[11px] font-medium text-ok hover:underline"
                >
                  {t('aiLibrary.skills.viewAgent', 'View')} →
                </Link>
              </div>
            ))
          ) : (
            <p className="px-4 pb-4 text-[12px] text-warn">
              {t(
                'aiLibrary.skills.orphanHint',
                'No agent uses this — bind or archive',
              )}
            </p>
          )}
        </section>

        {/* No wrapper heading: VersionHistoryPanel renders its own header, and
            it is the richer one (icon + current-version badge + refresh). Two
            stacked "Versions" titles with the v1 badge wedged between them was
            the acceptance finding. */}
        <section className="overflow-hidden rounded-lg border border-ink-800">
          <VersionHistoryPanel kind="skill" slug={skill.slug ?? String(skill.id)} />
        </section>
      </div>
      </div>

      {forkModalOpen && (
        <NewSkillModal
          existingSkills={allSkills.length > 0 ? allSkills : [skill]}
          initialForkFrom={skill.slug ?? String(skill.id)}
          onClose={() => setForkModalOpen(false)}
          onCreated={handleForkCreated}
        />
      )}
    </div>
  );
};

export default SkillEditor;
