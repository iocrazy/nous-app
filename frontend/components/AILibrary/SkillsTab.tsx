// frontend/components/AILibrary/SkillsTab.tsx
// Skills surface — two states (B3, spec 2026-08-02 §B3):
//
//   nothing selected → SkillGallery, full width. Cards carry the reverse
//                      index (who uses this) and flag orphans.
//   a skill selected → the editor, standalone and full width (spec §06).
//
// The rail used to be the only way in, which meant the answer to "is any of
// this unused" required opening skills one at a time. It is gone entirely
// now: the gallery owns list navigation and the tab strip is the way back.
//
// Selection is URL-driven via props (slug / filePath) so deep links like
// /ai-library/skills/:slug/files/references/example.md work on cold load
// and back-button navigation is natural.

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibrarySkill } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { SkillEditor } from './SkillEditor';
import { SkillGallery } from './SkillGallery';
import { AILibraryTabs } from './AILibraryTabs';
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

  const newFileSkill =
    newFileSkillSlug != null
      ? skills.find((s) => (s.slug ?? String(s.id)) === newFileSkillSlug) ?? null
      : null;

  const selectedSlug = slug ?? null;
  const selectedFilePath = filePath ?? null;

  if (loading) {
    return (
      <div className="p-6 text-sm text-ink-500">
        {t('aiLibrary.skills.loading', 'Loading skills...')}
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-danger-line bg-danger-soft p-4 text-sm text-danger">
          {t('aiLibrary.skills.loadError', 'Failed to load skills')}: {error}
        </div>
      </div>
    );
  }

  // Gallery state — cards ARE the navigation, so no rail here.
  if (!selectedSlug) {
    return (
      <>
        <SkillGallery
          skills={skills}
          onOpen={(s) => onSlugChange?.(s)}
          // Same strip and same button placement as the agents gallery — this
          // page is the other half of that surface, not a dead end.
          header={
            <AILibraryTabs
              active="skills"
              skillCount={skills.length}
              actions={
                <button
                  type="button"
                  onClick={() => setShowNewSkillModal(true)}
                  className="rounded-md border border-ink-700 px-2.5 py-1.5 text-[12px] text-ink-200 hover:bg-ink-800/60"
                >
                  {t('aiLibrary.skills.newSkill', '+ New Skill')}
                </button>
              }
            />
          }
        />
        {showNewSkillModal && (
          <NewSkillModal
            existingSkills={skills}
            onClose={() => setShowNewSkillModal(false)}
            onCreated={handleCreated}
          />
        )}
      </>
    );
  }

  // Editing is a standalone page (spec §06): no skill-list column beside the
  // editor. The gallery owns list navigation — a rail that duplicated it left
  // the editor squeezed into a third of the width and gave "+ New Skill" two
  // homes. The tab strip above is how you get back out.
  return (
    <div className="flex w-full flex-col">
      <div className="px-1 pt-6">
        {/* placement="detail": the Skills tab is how you get back to the
            gallery from here, so it must stay clickable. */}
        <AILibraryTabs active="skills" skillCount={skills.length} placement="detail" />
      </div>

      <div className="min-w-0 flex-1">
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
          onSelectFile={(path) =>
            path
              ? onFilePathChange?.(selectedSlug, path)
              : onSlugChange?.(selectedSlug)
          }
          onNewFile={() => setNewFileSkillSlug(selectedSlug)}
          onSkillForked={handleSkillForked}
          onSkillDeleted={handleSkillDeleted}
        />
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
