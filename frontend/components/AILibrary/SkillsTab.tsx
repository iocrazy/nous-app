// frontend/components/AILibrary/SkillsTab.tsx
// V6 paperclip-style split-pane:
//   Left rail  = SkillList (filter + per-skill file tree)
//   Right pane = SkillEditor (existing editor, hideBack, URL-driven tab)
//
// Selection is URL-driven via props (slug / filePath) so deep links like
// /ai-library/skills/:slug/files/references/example.md work on cold load
// and back-button navigation is natural.

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibrarySkill } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { SkillEditor } from './SkillEditor';
import { SkillList } from './SkillList';
import { NewSkillModal } from './NewSkillModal';
import { NewSkillFileModal } from './NewSkillFileModal';

interface SkillsTabProps {
  /** Currently selected skill slug (from URL). */
  slug?: string | null;
  /** Currently selected file path within the skill (from URL, optional). */
  filePath?: string | null;
  /** Called when the user picks a skill row in the left rail. */
  onSlugChange?: (slug: string) => void;
  /** Called when the user picks a file inside the expanded skill tree. */
  onFilePathChange?: (slug: string, filePath: string) => void;
}

export const SkillsTab: React.FC<SkillsTabProps> = ({
  slug,
  filePath,
  onSlugChange,
  onFilePathChange,
}) => {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<AILibrarySkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showNewSkillModal, setShowNewSkillModal] = useState(false);
  /** Slug of the skill whose file-creation modal is open, null when closed. */
  const [newFileSkillSlug, setNewFileSkillSlug] = useState<string | null>(null);

  const loadSkills = useCallback(async () => {
    try {
      setLoading(true);
      const list = await aiLibraryService.listSkills();
      // Preset first, then alphabetic — keeps the rail stable across reloads.
      const sorted = [...list].sort((a, b) => {
        const aPreset = a.is_public && a.team_id == null && a.project_id == null;
        const bPreset = b.is_public && b.team_id == null && b.project_id == null;
        if (aPreset !== bPreset) return aPreset ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      setSkills(sorted);
    } catch (err) {
      console.error('[SkillsTab] listSkills failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSkills();
  }, [loadSkills]);

  const handleCreated = async (newSlug: string) => {
    setShowNewSkillModal(false);
    await loadSkills();
    onSlugChange?.(newSlug);
  };

  const handleSkillForked = async (newSlug: string) => {
    await loadSkills();
    onSlugChange?.(newSlug);
  };

  const handleSkillDeleted = async () => {
    await loadSkills();
    onSlugChange?.('');
  };

  const handleFileCreated = async (slug: string, path: string) => {
    setNewFileSkillSlug(null);
    await loadSkills();
    if (onFilePathChange) onFilePathChange(slug, path);
  };

  const handleDeleteFile = async (slug: string, path: string) => {
    const ok = window.confirm(
      t(
        'aiLibrary.skills.deleteFileConfirm',
        `Delete ${path}? This cannot be undone.`,
        { path },
      ),
    );
    if (!ok) return;
    try {
      await aiLibraryService.deleteSkillFile(slug, path);
      await loadSkills();
      // If the user was viewing the file they just deleted, drop back to SKILL.md.
      if (slug === selectedSlug && path === selectedFilePath) {
        onSlugChange?.(slug);
      }
    } catch (err) {
      console.error('[SkillsTab] delete file failed:', err);
      window.alert(
        t('aiLibrary.skills.deleteFileError', 'Failed to delete: {{err}}', {
          err: err instanceof Error ? err.message : String(err),
        }),
      );
    }
  };

  const newFileSkill =
    newFileSkillSlug != null
      ? skills.find((s) => (s.slug ?? String(s.id)) === newFileSkillSlug) ?? null
      : null;

  const selectedSlug = slug ?? null;
  const selectedFilePath = filePath ?? null;

  return (
    <div className="flex h-full min-h-0 w-full">
      <SkillList
        skills={skills}
        activeSkillSlug={selectedSlug}
        activeFilePath={selectedFilePath}
        onSelectSkill={(s) => onSlugChange?.(s)}
        onSelectFile={(s, p) => {
          if (onFilePathChange) onFilePathChange(s, p);
          else onSlugChange?.(s);
        }}
        onNewSkill={() => setShowNewSkillModal(true)}
        onNewFile={(s) => setNewFileSkillSlug(s)}
        onDeleteFile={handleDeleteFile}
      />

      <div className="flex-1 min-w-0 h-full">
        {loading ? (
          <div className="p-6 text-sm text-zinc-500">
            {t('aiLibrary.skills.loading', 'Loading skills...')}
          </div>
        ) : error ? (
          <div className="p-6">
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
              {t('aiLibrary.skills.loadError', 'Failed to load skills')}: {error}
            </div>
          </div>
        ) : skills.length === 0 ? (
          <div className="flex h-full items-center justify-center p-6 text-sm text-zinc-500">
            {t('aiLibrary.skills.empty', 'No skills yet. Create your first one.')}
          </div>
        ) : !selectedSlug ? (
          <div className="flex h-full items-center justify-center p-6 text-sm text-zinc-500">
            {t(
              'aiLibrary.skills.selectPrompt',
              'Select a skill on the left to view and edit.',
            )}
          </div>
        ) : (
          <SkillEditor
            // ``key={selectedSlug}`` forces a full remount when the slug
            // changes so a pending in-flight fetch from the previous slug
            // can't land late and setSkill() the stale source skill on top
            // of the newly-forked one — caught by the QA tour where forking
            // Script Outline left the editor stuck on Script Outline until
            // the user manually reloaded.
            key={selectedSlug}
            slug={selectedSlug}
            filePath={selectedFilePath ?? ''}
            hideBack
            onBack={() => onSlugChange?.('')}
            onSkillForked={handleSkillForked}
            onSkillDeleted={handleSkillDeleted}
          />
        )}
      </div>

      {showNewSkillModal && (
        <NewSkillModal
          existingSkills={skills}
          onClose={() => setShowNewSkillModal(false)}
          onCreated={handleCreated}
        />
      )}

      {newFileSkill && (
        <NewSkillFileModal
          skill={newFileSkill}
          onClose={() => setNewFileSkillSlug(null)}
          onCreated={(path) =>
            handleFileCreated(
              newFileSkill.slug ?? String(newFileSkill.id),
              path,
            )
          }
        />
      )}
    </div>
  );
};

export default SkillsTab;
