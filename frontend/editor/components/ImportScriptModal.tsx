/**
 * ImportScriptModal — create a new script from imported screenplay/prose text.
 *
 * Two input tabs (paste text / upload a .fountain|.txt file ≤1MB), an
 * auto-detected format (Fountain vs Prose, user-overridable), and an async
 * submit that dispatches the import workflow, polls its task to completion, then
 * navigates to the new script in the v2 editor. Shared by the scripts-list
 * header and the editor cold-start screen — a fixed overlay, so it renders the
 * same in both. Do NOT confuse with the legacy `ImportScriptDialog`
 * (features/script) which uploads a document into an existing script.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Loader2, Upload, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useToast } from '../../components/Toast';
import { useTaskCompletion } from '../../hooks/useTaskCompletion';
import {
  MAX_IMPORT_CHARS,
  detectImportMode,
  importScreenplay,
  type ImportMode,
} from '../importService';

interface Props {
  projectId: string;
  onClose: () => void;
  /** Prefill the name field (e.g. from the current empty script). */
  defaultName?: string;
}

type Tab = 'paste' | 'upload';

export function ImportScriptModal({ projectId, onClose, defaultName }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId: string }>();
  const { addToast } = useToast();

  const [name, setName] = useState(defaultName ?? '');
  const [tab, setTab] = useState<Tab>('paste');
  const [content, setContent] = useState('');
  const [mode, setMode] = useState<ImportMode>('prose');
  const [modeOverridden, setModeOverridden] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const newScriptIdRef = useRef<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  // Set content and re-suggest the mode (unless the user pinned it manually).
  const applyContent = useCallback(
    (text: string) => {
      setContent(text);
      if (!modeOverridden) setMode(detectImportMode(text));
    },
    [modeOverridden],
  );

  const onFile = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      setFileError(null);
      if (!/\.(fountain|txt)$/i.test(file.name)) {
        setFileError(t('editor.importErrorType'));
        return;
      }
      if (file.size > MAX_IMPORT_CHARS) {
        setFileError(t('editor.importErrorTooLarge'));
        return;
      }
      try {
        applyContent(await file.text());
      } catch {
        setFileError(t('editor.importErrorType'));
      }
    },
    [applyContent, t],
  );

  const chooseMode = (next: ImportMode) => {
    setMode(next);
    setModeOverridden(true);
  };

  // The scenes are written server-side by the workflow; navigate to the new
  // script once the task settles so the editor opens on a populated script.
  useTaskCompletion(taskId, {
    onComplete: () => {
      addToast(t('editor.importSuccess'), 'success');
      const scriptId = newScriptIdRef.current;
      onClose();
      if (scriptId) {
        navigate(`/team/${teamId}/projects/${projectId}/scripts/${scriptId}`);
      }
    },
    onError: (task) => {
      addToast(task.error_msg || t('editor.importFailed'), 'error');
      setSubmitting(false);
      setTaskId(null);
    },
  });

  const canSubmit = name.trim().length > 0 && content.trim().length > 0 && !submitting;

  const handleSubmit = useCallback(async () => {
    if (!name.trim() || !content.trim() || submitting) return;
    if (content.length > MAX_IMPORT_CHARS) {
      addToast(t('editor.importErrorTooLarge'), 'error');
      return;
    }
    setSubmitting(true);
    try {
      const res = await importScreenplay({
        project_id: projectId,
        name: name.trim(),
        mode,
        content,
      });
      newScriptIdRef.current = res.script_id;
      setTaskId(res.task_id);
    } catch (err) {
      console.error('[ImportScriptModal] import failed:', err);
      addToast(err instanceof Error ? err.message : t('editor.importFailed'), 'error');
      setSubmitting(false);
    }
  }, [name, content, mode, projectId, submitting, addToast, t]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div className="w-full max-w-lg rounded-xl bg-ink-900 border border-ink-700 shadow-2xl p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-ink-100">{t('editor.importTitle')}</h3>
          <button
            onClick={onClose}
            disabled={submitting}
            className="text-ink-500 hover:text-ink-300 transition-colors disabled:opacity-40"
          >
            <X size={16} />
          </button>
        </div>

        <label className="block text-xs text-ink-400 mb-1.5">{t('editor.importName')}</label>
        <input
          ref={nameRef}
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('editor.importNamePlaceholder')}
          className="w-full rounded-lg bg-ink-800 border border-ink-700 px-3 py-2 text-sm text-ink-100 placeholder-ink-500 focus:outline-none focus:border-indigo-500 transition-colors"
        />

        {/* Source tabs */}
        <div className="mt-4 flex gap-1 rounded-lg bg-ink-800 p-1">
          {(['paste', 'upload'] as Tab[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                tab === key
                  ? 'bg-indigo-600 text-white'
                  : 'text-ink-400 hover:text-ink-200'
              }`}
            >
              {t(key === 'paste' ? 'editor.importTabPaste' : 'editor.importTabUpload')}
            </button>
          ))}
        </div>

        {tab === 'paste' ? (
          <textarea
            value={content}
            onChange={(e) => applyContent(e.target.value)}
            placeholder={t('editor.importPastePlaceholder')}
            rows={8}
            className="mt-3 w-full resize-none rounded-lg bg-ink-800 border border-ink-700 px-3 py-2 text-sm text-ink-100 placeholder-ink-500 focus:outline-none focus:border-indigo-500 transition-colors font-mono"
          />
        ) : (
          <div className="mt-3">
            <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-ink-700 bg-ink-800/50 px-3 py-6 text-center hover:border-ink-600 transition-colors">
              <Upload size={18} className="text-ink-500" />
              <span className="text-xs text-ink-400">{t('editor.importUploadHint')}</span>
              <input
                type="file"
                accept=".fountain,.txt"
                data-testid="import-file-input"
                className="hidden"
                onChange={(e) => void onFile(e.target.files?.[0])}
              />
            </label>
            {content && !fileError && (
              <p className="mt-2 text-[11px] text-ink-500">
                {t('editor.importLoadedChars', { count: content.length })}
              </p>
            )}
            {fileError && <p className="mt-2 text-xs text-red-400">{fileError}</p>}
          </div>
        )}

        {/* Format selector */}
        <div className="mt-4 flex items-center gap-3">
          <span className="text-xs text-ink-400">{t('editor.importModeLabel')}</span>
          <div className="flex gap-1 rounded-lg bg-ink-800 p-1">
            {(['fountain', 'prose'] as ImportMode[]).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => chooseMode(key)}
                className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                  mode === key ? 'bg-indigo-600 text-white' : 'text-ink-400 hover:text-ink-200'
                }`}
              >
                {t(key === 'fountain' ? 'editor.importModeFountain' : 'editor.importModeProse')}
              </button>
            ))}
          </div>
        </div>

        {mode === 'prose' && (
          <p className="mt-2 text-[11px] text-ink-500">{t('editor.importProseHint')}</p>
        )}

        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onClose}
            disabled={submitting}
            className="px-3 py-1.5 text-xs font-medium text-ink-400 hover:text-ink-200 rounded-lg hover:bg-ink-800 transition-colors disabled:opacity-40"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting && <Loader2 size={12} className="animate-spin" />}
            {submitting ? t('editor.importing') : t('editor.importSubmit')}
          </button>
        </div>
      </div>
    </div>
  );
}
