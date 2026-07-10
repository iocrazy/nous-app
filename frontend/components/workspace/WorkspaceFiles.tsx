/**
 * WorkspaceFiles — the unified Files module (spec frame: "Files 模块",
 * decision G5: Files absorbs Output — one folder browser for uploaded
 * assets, project files, and generated renders, filtered by type chips).
 *
 * v1 simplifications (timeboxed, PR-10b Wave 2):
 *   - "All" merges the current folder's subfolders + files with the
 *     project's renders (renders aren't folder-scoped, so they're always
 *     appended regardless of which folder you're in).
 *   - Double-clicking a file is a no-op beyond the title tooltip — wiring
 *     the existing FileInfoPanel/preview flow in is a fit-and-finish
 *     follow-up, not attempted here to stay in scope.
 *   - Renders pagination: first page only (limit 60), no "load more" yet.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Folder, Film, Image as ImageIcon, FileText, Upload as UploadIcon, Loader2 } from 'lucide-react';
import {
  fetchProjectFiles,
  fetchProjectFolders,
  fetchProjectRenders,
  uploadFile,
} from '../../services/projectsService';
import { getApiUrl } from '../../utils/apiConfig';
import { formatRelativeTime } from '../../utils/relativeTime';
import { useToast } from '../Toast';
import type { EpisodeProgress, ProjectFile, ProjectFolder, RenderItem } from '../../types';

export type FilesChip = 'all' | 'media' | 'renders' | 'docs';

interface WorkspaceFilesProps {
  projectId: string;
  currentEpisode: EpisodeProgress | null;
  initialChip?: FilesChip;
  initialEpisodeFilterOn?: boolean;
}

type GridItem =
  | { kind: 'folder'; data: ProjectFolder }
  | { kind: 'file'; data: ProjectFile }
  | { kind: 'render'; data: RenderItem };

function isMediaFile(f: ProjectFile): boolean {
  return f.file_type === 'video' || f.file_type === 'image';
}

function renderUrl(r: RenderItem): string {
  const path = r.media_kind === 'video' ? 'stream' : 'cover';
  return `${getApiUrl()}/api/v1/generated-media/${r.id}/${path}`;
}

export function WorkspaceFiles({
  projectId,
  currentEpisode,
  initialChip = 'all',
  initialEpisodeFilterOn = false,
}: WorkspaceFilesProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [chip, setChip] = useState<FilesChip>(initialChip);
  const [epFilterOn, setEpFilterOn] = useState(initialEpisodeFilterOn);

  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
  const [folderChain, setFolderChain] = useState<ProjectFolder[]>([]);
  const [folders, setFolders] = useState<ProjectFolder[]>([]);
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [renders, setRenders] = useState<RenderItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      fetchProjectFolders(projectId, currentFolderId),
      fetchProjectFiles(projectId, false, currentFolderId),
    ])
      .then(([f, fl]) => {
        if (cancelled) return;
        setFolders(f);
        setFiles(fl);
      })
      .catch((err) => console.error('[WorkspaceFiles] failed to load folder contents:', err))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, currentFolderId]);

  useEffect(() => {
    let cancelled = false;
    fetchProjectRenders(projectId, {
      episodeId: epFilterOn ? currentEpisode?.episode_id ?? null : null,
      limit: 60,
    })
      .then((page) => {
        if (!cancelled) setRenders(page.items);
      })
      .catch((err) => console.error('[WorkspaceFiles] failed to load renders:', err));
    return () => {
      cancelled = true;
    };
  }, [projectId, epFilterOn, currentEpisode?.episode_id]);

  const mediaFiles = useMemo(() => files.filter(isMediaFile), [files]);
  const docFiles = useMemo(() => files.filter((f) => !isMediaFile(f)), [files]);

  const items: GridItem[] = useMemo(() => {
    if (chip === 'all') {
      return [
        ...folders.map((f): GridItem => ({ kind: 'folder', data: f })),
        ...files.map((f): GridItem => ({ kind: 'file', data: f })),
        ...renders.map((r): GridItem => ({ kind: 'render', data: r })),
      ];
    }
    if (chip === 'media') return mediaFiles.map((f): GridItem => ({ kind: 'file', data: f }));
    if (chip === 'docs') return docFiles.map((f): GridItem => ({ kind: 'file', data: f }));
    return renders.map((r): GridItem => ({ kind: 'render', data: r }));
  }, [chip, folders, files, renders, mediaFiles, docFiles]);

  const navigateToFolder = (folder: ProjectFolder | null) => {
    if (!folder) {
      setCurrentFolderId(null);
      setFolderChain([]);
      return;
    }
    setCurrentFolderId(folder.id);
    setFolderChain((chain) => {
      const idx = chain.findIndex((f) => f.id === folder.id);
      if (idx >= 0) return chain.slice(0, idx + 1);
      return [...chain, folder];
    });
  };

  const handleUploadClick = () => fileInputRef.current?.click();

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await uploadFile(projectId, file);
      const fl = await fetchProjectFiles(projectId, false, currentFolderId);
      setFiles(fl);
    } catch (err) {
      console.error('[WorkspaceFiles] upload failed:', err);
      addToast(t('common.error'), 'error');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const chips: { key: FilesChip; labelKey: string }[] = [
    { key: 'all', labelKey: 'projects.workspace.files.chipAll' },
    { key: 'media', labelKey: 'projects.workspace.files.chipMedia' },
    { key: 'renders', labelKey: 'projects.workspace.files.chipRenders' },
    { key: 'docs', labelKey: 'projects.workspace.files.chipDocs' },
  ];

  return (
    <div data-testid="ws-files" className="py-3">
      <input
        ref={fileInputRef}
        type="file"
        data-testid="ws-files-upload-input"
        className="hidden"
        onChange={handleFileChange}
      />

      <div className="flex items-center gap-2 flex-wrap mb-3">
        <div data-testid="ws-files-breadcrumb" className="flex items-center gap-1 text-[12px] text-ink-400">
          <button
            data-testid="ws-files-breadcrumb-root"
            onClick={() => navigateToFolder(null)}
            className="hover:text-ink-100 transition-colors"
          >
            {t('projects.workspace.files.breadcrumbRoot')}
          </button>
          {folderChain.map((f) => (
            <span key={f.id} className="flex items-center gap-1">
              <span>/</span>
              <button
                data-testid={`ws-files-breadcrumb-${f.id}`}
                onClick={() => navigateToFolder(f)}
                className="hover:text-ink-100 transition-colors"
              >
                {f.name}
              </button>
            </span>
          ))}
          <span>/</span>
        </div>

        <div className="flex-1" />

        {chips.map((c) => (
          <button
            key={c.key}
            data-testid={`ws-files-chip-${c.key}`}
            onClick={() => setChip(c.key)}
            className={`text-[12px] rounded-full px-3 py-1 border transition-colors ${
              chip === c.key
                ? 'bg-indigo-500/10 text-indigo-300 border-transparent font-medium'
                : 'text-ink-300 border-ink-700 hover:border-ink-500'
            }`}
          >
            {t(c.labelKey)}
          </button>
        ))}

        {chip === 'renders' && (
          <button
            data-testid="ws-files-ep-filter-toggle"
            onClick={() => setEpFilterOn((v) => !v)}
            disabled={!currentEpisode}
            className={`text-[11px] rounded-full px-2.5 py-1 border transition-colors disabled:opacity-40 ${
              epFilterOn
                ? 'bg-indigo-500/10 text-indigo-300 border-transparent font-medium'
                : 'text-ink-400 border-ink-700 hover:border-ink-500'
            }`}
          >
            {t('projects.workspace.files.currentEpOnly')}
          </button>
        )}

        <button
          data-testid="ws-files-upload-btn"
          onClick={handleUploadClick}
          disabled={uploading}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-500 hover:bg-indigo-400 disabled:opacity-50 text-ink-950 font-semibold text-[12.5px] px-3 py-1.5 transition-colors"
        >
          {uploading ? <Loader2 size={13} className="animate-spin" /> : <UploadIcon size={13} />}
          {t('projects.workspace.files.upload')}
        </button>
      </div>

      {!loading && items.length === 0 && (
        <div data-testid="ws-files-empty" className="text-center text-xs text-ink-600 py-10">
          {t(
            chip === 'renders' ? 'projects.workspace.files.emptyRenders' : 'projects.workspace.files.empty',
          )}
        </div>
      )}

      <div
        data-testid="ws-files-grid"
        className="grid gap-2.5"
        style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))' }}
      >
        {items.map((item) => {
          if (item.kind === 'folder') {
            const f = item.data;
            return (
              <div
                key={`folder-${f.id}`}
                data-testid={`ws-files-item-folder-${f.id}`}
                onDoubleClick={() => navigateToFolder(f)}
                title={f.name}
                className="rounded-xl border border-ink-800 bg-ink-900/40 p-2.5 cursor-pointer hover:border-ink-600 transition-colors"
              >
                <div className="h-12 rounded-lg bg-ink-800/60 grid place-items-center mb-2 text-ink-400">
                  <Folder size={18} />
                </div>
                <div className="text-[12px] font-medium text-ink-100 truncate">{f.name}</div>
                <div className="text-[10px] text-ink-500 mt-0.5">
                  {t('projects.workspace.files.folder')}
                </div>
              </div>
            );
          }
          if (item.kind === 'file') {
            const f = item.data;
            const Icon = f.file_type === 'video' ? Film : f.file_type === 'image' ? ImageIcon : FileText;
            return (
              <div
                key={`file-${f.id}`}
                data-testid={`ws-files-item-file-${f.id}`}
                title={f.filename}
                className="rounded-xl border border-ink-800 bg-ink-900/40 p-2.5 hover:border-ink-600 transition-colors"
              >
                <div className="h-12 rounded-lg bg-ink-800/60 grid place-items-center mb-2 text-ink-400">
                  <Icon size={18} />
                </div>
                <div className="text-[12px] font-medium text-ink-100 truncate">{f.filename}</div>
                <div className="font-mono text-[10px] text-ink-500 mt-0.5">
                  {f.file_type ?? '—'}
                </div>
              </div>
            );
          }
          const r = item.data;
          return (
            <a
              key={`render-${r.id}`}
              data-testid={`ws-files-item-render-${r.id}`}
              href={renderUrl(r)}
              target="_blank"
              rel="noreferrer"
              title={t('projects.workspace.files.render')}
              className="rounded-xl border border-ink-800 bg-ink-900/40 p-2.5 hover:border-ink-600 transition-colors block"
            >
              {r.media_kind === 'image' ? (
                <img
                  src={renderUrl(r)}
                  alt=""
                  className="h-12 w-full rounded-lg object-cover mb-2 bg-ink-800/60"
                />
              ) : (
                <div className="h-12 rounded-lg bg-ink-800/60 grid place-items-center mb-2 text-ink-400">
                  <Film size={18} />
                </div>
              )}
              <div className="text-[12px] font-medium text-ink-100 truncate">
                {t('projects.workspace.files.render')}
              </div>
              <div className="font-mono text-[10px] text-ink-500 mt-0.5">
                {t('projects.workspace.files.render')} · {formatRelativeTime(r.created_at, t)}
              </div>
            </a>
          );
        })}
      </div>
    </div>
  );
}

export default WorkspaceFiles;
