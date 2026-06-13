// frontend/components/AILibrary/NewSkillFileModal.tsx
//
// Modal for creating a new file OR folder inside an existing skill.
//
// MediaHub skills are DB-first, so "folders" are path-derived: a file at
// `references/notes.md` implicitly creates the `references/` folder in the
// tree. To let users create an EMPTY folder (paperclip-style), we drop a
// `.gitkeep` placeholder file inside it — same convention git uses.
//
// File type is inferred from the extension so the editor's View/Code toggle
// picks the right renderer.

import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { aiLibraryService } from '../../services/aiLibraryService';
import type { AILibrarySkill, AILibrarySkillFile } from '../../types';

type Mode = 'file' | 'folder';

interface NewSkillFileModalProps {
  skill: AILibrarySkill;
  /** Preselected folder path (e.g. clicking "+" next to `references/`). */
  initialDir?: string;
  onClose: () => void;
  onCreated: (path: string) => void;
}

type FileType = AILibrarySkillFile['file_type'];

/** Infer the backend file_type enum from a path's extension. */
export function inferFileType(path: string): FileType {
  const ext = path.split('.').pop()?.toLowerCase() ?? '';
  if (ext === 'md' || ext === 'markdown') return 'markdown';
  const scriptExts = new Set([
    'js',
    'jsx',
    'ts',
    'tsx',
    'py',
    'sh',
    'bash',
    'zsh',
    'rb',
    'rs',
    'go',
    'java',
    'c',
    'cc',
    'cpp',
    'cs',
    'php',
    'pl',
    'lua',
    'swift',
    'kt',
    'scala',
    'dart',
    'r',
  ]);
  if (scriptExts.has(ext)) return 'script';
  return 'text-asset';
}

/** Disallow control chars + backslash + leading/trailing slash; forbid `..`. */
function isSafeSegment(seg: string): boolean {
  if (!seg || seg === '.' || seg === '..') return false;
  // Forbid any char that breaks URL path param: control chars, backslash, ?, #
  return !/[\x00-\x1f\\?#]/.test(seg);
}

function normalizePath(raw: string): string {
  return raw.trim().replace(/^\/+|\/+$/g, '').replace(/\/+/g, '/');
}

function validatePath(path: string, mustHaveFilename: boolean): string | null {
  if (!path) return 'Path required.';
  const segments = path.split('/');
  for (const s of segments) {
    if (!isSafeSegment(s)) return `Invalid segment: "${s}"`;
  }
  if (mustHaveFilename) {
    const leaf = segments[segments.length - 1];
    if (!leaf.includes('.')) {
      return 'File name needs an extension (e.g. `notes.md`, `helper.js`).';
    }
  }
  return null;
}

export const NewSkillFileModal: React.FC<NewSkillFileModalProps> = ({
  skill,
  initialDir,
  onClose,
  onCreated,
}) => {
  const { t } = useTranslation();
  const [mode, setMode] = useState<Mode>('file');
  const [path, setPath] = useState(initialDir ? `${initialDir}/` : '');
  const [content, setContent] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const normalizedPath = useMemo(() => normalizePath(path), [path]);
  const pathError = useMemo(
    () => (path ? validatePath(normalizedPath, mode === 'file') : null),
    [path, normalizedPath, mode],
  );

  // Existing path clash — can't overwrite an existing file via this modal.
  const clash = useMemo(() => {
    if (!normalizedPath) return false;
    const finalPath =
      mode === 'folder' ? `${normalizedPath}/.gitkeep` : normalizedPath;
    return (skill.files ?? []).some((f) => f.path === finalPath);
  }, [skill.files, normalizedPath, mode]);

  const canSubmit =
    !submitting && !!normalizedPath && !pathError && !clash;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const slug = skill.slug ?? String(skill.id);
      if (mode === 'folder') {
        const placeholderPath = `${normalizedPath}/.gitkeep`;
        await aiLibraryService.upsertSkillFile(
          slug,
          placeholderPath,
          '',
          'text-asset',
        );
        onCreated(placeholderPath);
      } else {
        await aiLibraryService.upsertSkillFile(
          slug,
          normalizedPath,
          content,
          inferFileType(normalizedPath),
        );
        onCreated(normalizedPath);
      }
    } catch (err) {
      console.error('[NewSkillFileModal] upsert failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-lg border border-ink-800 bg-ink-900 p-6 shadow-xl">
        <h2 className="text-lg font-semibold text-ink-100">
          {t('aiLibrary.skills.newFileTitle', 'New file or folder')}
        </h2>
        <p className="mt-1 text-xs text-ink-500">
          {t(
            'aiLibrary.skills.newFileHint',
            'Add files under references/, scripts/, or assets/. Folders are path-derived.',
          )}
        </p>

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          {/* Mode picker */}
          <fieldset className="flex gap-2">
            <ModeButton
              active={mode === 'file'}
              onClick={() => setMode('file')}
              label={t('aiLibrary.skills.newFileModeFile', 'File')}
              disabled={submitting}
            />
            <ModeButton
              active={mode === 'folder'}
              onClick={() => setMode('folder')}
              label={t('aiLibrary.skills.newFileModeFolder', 'Folder')}
              disabled={submitting}
            />
          </fieldset>

          {/* Path */}
          <div>
            <label className="block text-xs font-medium text-ink-400">
              {mode === 'file'
                ? t('aiLibrary.skills.newFilePathLabel', 'File path')
                : t('aiLibrary.skills.newFolderPathLabel', 'Folder path')}
            </label>
            <input
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder={
                mode === 'file'
                  ? 'references/notes.md'
                  : 'references/research'
              }
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 font-mono text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
              disabled={submitting}
              autoFocus
              required
            />
            {pathError && (
              <p className="mt-1 text-xs text-red-400">{pathError}</p>
            )}
            {clash && !pathError && (
              <p className="mt-1 text-xs text-red-400">
                {t(
                  'aiLibrary.skills.newFilePathClash',
                  'A file already exists at that path.',
                )}
              </p>
            )}
            {mode === 'file' && normalizedPath && !pathError && !clash && (
              <p className="mt-1 text-xs text-ink-500">
                {t('aiLibrary.skills.newFileTypeHint', 'Type: {{t}}', {
                  t: inferFileType(normalizedPath),
                })}
              </p>
            )}
          </div>

          {/* Initial content (file only) */}
          {mode === 'file' && (
            <div>
              <label className="block text-xs font-medium text-ink-400">
                {t('aiLibrary.skills.newFileContentLabel', 'Initial content')}
                <span className="ml-1 text-ink-600">
                  ({t('common.optional', 'optional')})
                </span>
              </label>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={6}
                placeholder={
                  inferFileType(normalizedPath) === 'markdown'
                    ? '# Notes\n\nYour content here…'
                    : '// leave empty to start blank'
                }
                className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 font-mono text-xs text-ink-100 focus:border-indigo-500 focus:outline-none"
                disabled={submitting}
              />
            </div>
          )}

          {error && (
            <div className="rounded-md border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="rounded-md border border-ink-700 px-4 py-2 text-sm text-ink-300 hover:bg-ink-800"
            >
              {t('common.cancel', 'Cancel')}
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting
                ? t('common.saving', 'Creating…')
                : t('common.create', 'Create')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

interface ModeButtonProps {
  active: boolean;
  disabled: boolean;
  label: string;
  onClick: () => void;
}

const ModeButton: React.FC<ModeButtonProps> = ({
  active,
  disabled,
  label,
  onClick,
}) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    className={`flex-1 rounded-md border px-3 py-2 text-sm transition-colors ${
      active
        ? 'border-indigo-500 bg-indigo-500/10 text-indigo-200'
        : 'border-ink-700 bg-ink-800 text-ink-300 hover:bg-ink-750'
    } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
  >
    {label}
  </button>
);

export default NewSkillFileModal;
