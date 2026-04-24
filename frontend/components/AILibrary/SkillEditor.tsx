// frontend/components/AILibrary/SkillEditor.tsx
//
// Paperclip-style SkillPane — ported 1:1 from paperclip
// (ui/src/pages/CompanySkills.tsx::SkillPane) while keeping MediaHub's
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
// - MediaHub has "Bundled preset" vs "User/Team/Project" scopes — no
//   GitHub / skills.sh / URL source variants (and thus no "Check for
//   updates" workflow).
// - "Used by" not yet returned by GET /skills/:slug — shows a dash
//   placeholder until the backend adds it.

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
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

interface SkillEditorProps {
  slug: string;
  onBack: () => void;
  onSkillForked?: (newSlug: string) => void;
  /** URL-driven active file. ``''`` or null = SKILL.md. */
  filePath?: string | null;
  /** V6 split-pane hides the in-editor Back button. */
  hideBack?: boolean;
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
function skillSource(skill: AILibrarySkill) {
  const bundled =
    skill.is_public && skill.team_id == null && skill.project_id == null;
  if (bundled) {
    return {
      icon: Package,
      label: 'MediaHub bundled',
      managedLabel: 'Bundled MediaHub preset (read-only)',
    };
  }
  if (skill.team_id != null) {
    return {
      icon: Users,
      label: `Team: ${skill.team_name ?? skill.team_id}`,
      managedLabel: 'Team skill',
    };
  }
  if (skill.project_id != null) {
    return {
      icon: Users,
      label: `Project: ${skill.project_name ?? skill.project_id}`,
      managedLabel: 'Project skill',
    };
  }
  return {
    icon: UserIcon,
    label: 'Private',
    managedLabel: 'Personal skill',
  };
}

export const SkillEditor: React.FC<SkillEditorProps> = ({
  slug,
  onBack,
  onSkillForked,
  filePath = null,
  hideBack = false,
}) => {
  const { t } = useTranslation();
  const { userProfile } = useAuth();
  const { addToast } = useToast();
  const isAdmin = userProfile?.role === 'admin';

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
            className="mb-3 text-sm text-zinc-400 hover:text-zinc-100"
          >
            ← Back
          </button>
        )}
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          Failed to load skill: {error}
        </div>
      </div>
    );
  }

  if (!skill) {
    return <div className="p-6 text-sm text-zinc-500">Loading...</div>;
  }

  const isPreset =
    skill.is_public && skill.team_id == null && skill.project_id == null;
  const editable = !isPreset;
  const editableReason = isPreset
    ? t(
        'aiLibrary.skills.bundledReadOnlyHint',
        'Bundled MediaHub skills are read-only. Fork to edit.',
      )
    : '';
  const canDelete = isPreset ? isAdmin : true;
  const source = skillSource(skill);
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
      onBack();
    } catch (err) {
      console.error('[SkillEditor] deleteSkill failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      addToast(msg, 'error');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden">
      {/* ── Header ────────────────────────────────────────────────── */}
      <div className="border-b border-zinc-800/80 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="flex items-center gap-2 truncate text-2xl font-semibold text-zinc-100">
              <SourceIcon className="h-5 w-5 shrink-0 text-zinc-500" />
              {skill.name}
            </h1>
            {skill.description && (
              <p className="mt-2 max-w-3xl text-sm text-zinc-400">
                {skill.description}
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {canDelete && (
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting || saving}
                className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-zinc-400 transition-colors hover:bg-zinc-800/60 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-50"
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
                className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-zinc-400 transition-colors hover:bg-zinc-800/60 hover:text-zinc-100"
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
                className="inline-flex items-center gap-1.5 rounded-md bg-indigo-500/10 px-2.5 py-1.5 text-sm text-indigo-300 transition-colors hover:bg-indigo-500/20"
                title={editableReason}
              >
                <GitFork className="h-3.5 w-3.5" />
                {t('aiLibrary.skills.forkToEdit', 'Fork to edit')}
              </button>
            )}
          </div>
        </div>

        {/* Metadata strip */}
        <div className="mt-4 space-y-3 border-t border-zinc-800/60 pt-4 text-sm">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <MetaLabel
              label={t('aiLibrary.skills.metaSource', 'Source')}
              icon={SourceIcon}
              value={source.label}
            />
            <MetaLabel
              label={t('aiLibrary.skills.metaKey', 'Key')}
              mono
              value={skill.slug ?? String(skill.id)}
            />
            <MetaLabel
              label={t('aiLibrary.skills.metaMode', 'Mode')}
              value={editable ? 'Editable' : 'Read only'}
            />
          </div>
          <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
            <span className="text-[11px] uppercase tracking-[0.18em] text-zinc-500">
              {t('aiLibrary.skills.metaUsedBy', 'Used by')}
            </span>
            <span className="text-zinc-500">
              {t('aiLibrary.skills.usedByPlaceholder', 'No agents attached')}
            </span>
          </div>
        </div>

        {!editable && (
          <div className="mt-3 rounded border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[12px] text-amber-300/90">
            {editableReason}
          </div>
        )}
      </div>

      {/* ── Sub-header: file path + toggle ────────────────────────── */}
      <div className="border-b border-zinc-800/80 px-5 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate font-mono text-sm text-zinc-300">
              {activeTab}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {editMode && editable ? (
              <>
                <button
                  type="button"
                  onClick={() => {
                    setEditMode(false);
                    setDrafts((d) => ({ ...d, [activeTab]: activeContent }));
                  }}
                  disabled={saving}
                  className="rounded-md px-2.5 py-1.5 text-sm text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100 disabled:opacity-50"
                >
                  {t('common.cancel', 'Cancel')}
                </button>
                <button
                  type="button"
                  onClick={save}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  <Save className="h-3.5 w-3.5" />
                  {saving
                    ? t('common.saving', 'Saving...')
                    : t('common.save', 'Save')}
                </button>
              </>
            ) : isMarkdown ? (
              <div className="flex items-center overflow-hidden rounded border border-zinc-800">
                <button
                  type="button"
                  onClick={() => setViewMode('preview')}
                  className={`flex items-center gap-1.5 px-3 py-1 text-sm transition-colors ${
                    viewMode === 'preview'
                      ? 'bg-zinc-800 text-zinc-100'
                      : 'text-zinc-500 hover:text-zinc-200'
                  }`}
                >
                  <Eye className="h-3.5 w-3.5" />
                  {t('aiLibrary.skills.viewTab', 'View')}
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode('code')}
                  className={`flex items-center gap-1.5 border-l border-zinc-800 px-3 py-1 text-sm transition-colors ${
                    viewMode === 'code'
                      ? 'bg-zinc-800 text-zinc-100'
                      : 'text-zinc-500 hover:text-zinc-200'
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
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
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
              className="h-[560px] w-full border-0 bg-transparent p-0 font-mono text-sm text-zinc-200 focus:outline-none"
              spellCheck={false}
            />
          )
        ) : isMarkdown && viewMode === 'preview' ? (
          <MarkdownBody source={activeBody} />
        ) : (
          <pre className="whitespace-pre-wrap break-words font-mono text-sm text-zinc-200">
            <code>{activeContent}</code>
          </pre>
        )}
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

// ─── Inline metadata cell ──────────────────────────────────────
const MetaLabel: React.FC<{
  label: string;
  value: string;
  icon?: React.ElementType;
  mono?: boolean;
}> = ({ label, value, icon: Icon, mono = false }) => (
  <div className="flex items-center gap-2">
    <span className="text-[11px] uppercase tracking-[0.18em] text-zinc-500">
      {label}
    </span>
    <span className="flex items-center gap-1.5 text-zinc-300">
      {Icon && <Icon className="h-3.5 w-3.5 text-zinc-500" />}
      <span className={mono ? 'font-mono text-xs' : ''}>{value}</span>
    </span>
  </div>
);

export default SkillEditor;
