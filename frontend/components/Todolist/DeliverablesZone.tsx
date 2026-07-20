/**
 * DeliverablesZone — issue-side deliverable dropzone (M2-W1).
 *
 * Shown on a project-backed issue's detail view. Files dropped/browsed here are
 * uploaded straight into the owning project's files (via the issue-context
 * upload endpoint): a workflow-node mirror issue routes them into the node's
 * stage folder automatically, a plain project issue lands them in the project
 * root. Each already-filed file renders with a "Filed ✓" chip. The list is the
 * server's source-issue view — no client-side folder guessing.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { UploadCloud, FileCheck2, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { fetchProjectFiles, uploadFile } from '../../services/projectsService';
import type { ProjectFile } from '../../types';
import { useToast } from '../Toast';

interface DeliverablesZoneProps {
  projectId: string;
  issueId: number;
  /** True when the issue mirrors a workflow node (files route to its stage folder). */
  isStageMirror: boolean;
  projectName: string;
  /** The node/stage name, when this is a workflow-node mirror issue. */
  stageName?: string;
}

export const DeliverablesZone: React.FC<DeliverablesZoneProps> = ({
  projectId,
  issueId,
  isStageMirror,
  projectName,
  stageName,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchProjectFiles(projectId, false, null, String(issueId));
      setFiles(list);
    } catch (err) {
      console.error('[DeliverablesZone] load failed', err);
    } finally {
      setLoading(false);
    }
  }, [projectId, issueId]);

  useEffect(() => { void refresh(); }, [refresh]);

  const doUpload = useCallback(
    async (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return;
      setUploading(true);
      try {
        for (const f of Array.from(fileList)) {
          await uploadFile(projectId, f, undefined, String(issueId));
        }
        await refresh();
        addToast(t('projects.workflow.deliverables.filedToast'), 'success');
      } catch (err) {
        console.error('[DeliverablesZone] upload failed', err);
        addToast(err instanceof Error ? err.message : t('projects.workflow.deliverables.uploadFailed'), 'error');
      } finally {
        setUploading(false);
      }
    },
    [projectId, issueId, refresh, addToast, t],
  );

  const dropTarget = stageName
    ? `${projectName} / ${stageName}`
    : projectName;

  return (
    <div data-testid="deliverables-zone" className="mt-6">
      <div className="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-wider text-ink-500">
        <FileCheck2 size={13} />
        {t('projects.workflow.deliverables.title')}
      </div>

      <label
        data-testid="deliverables-dropzone"
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void doUpload(e.dataTransfer.files);
        }}
        className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-6 text-center transition ${
          dragOver
            ? 'border-[var(--accent-border)] bg-[var(--accent-soft)]'
            : 'border-ink-700 bg-ink-900/40 hover:border-ink-600'
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          data-testid="deliverables-input"
          onChange={(e) => void doUpload(e.target.files)}
        />
        {uploading ? (
          <Loader2 size={20} className="animate-spin text-ink-400" />
        ) : (
          <UploadCloud size={20} className="text-ink-400" />
        )}
        <span className="text-[13px] text-ink-300">
          {t('projects.workflow.deliverables.dropHint', { target: dropTarget })}
        </span>
      </label>

      {loading ? (
        <div className="mt-3 text-[13px] text-ink-500 italic">
          {t('projects.workflow.deliverables.loading')}
        </div>
      ) : files.length === 0 ? (
        <div className="mt-3 text-[13px] text-ink-600 italic">
          {t('projects.workflow.deliverables.empty')}
        </div>
      ) : (
        <ul className="mt-3 flex flex-col gap-1.5" data-testid="deliverables-list">
          {files.map((f) => (
            <li
              key={f.id}
              data-testid="deliverables-file-row"
              className="flex items-center gap-2 rounded-md border border-ink-800 bg-ink-900/40 px-3 py-2 text-[13px] text-ink-200"
            >
              <FileCheck2 size={14} className="shrink-0 text-emerald-400" />
              <span className="min-w-0 flex-1 truncate">{f.filename}</span>
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[11px] text-emerald-300">
                {t('projects.workflow.deliverables.filedChip')}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};
