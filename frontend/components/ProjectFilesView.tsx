import React, { useState, useEffect, useRef, useMemo } from 'react';
import { ArrowLeft, Upload, Link, LayoutGrid, LayoutList, Loader2, FileText, FolderOpen, ChevronDown, Plus, ChevronRight, Folder as FolderIcon, Search, Users, Inbox } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Project, ProjectFile, ProjectFolder, ReviewStatus } from '../types';
import { fetchProjectFiles, uploadFile, fetchProjectFolders, createProjectFolder, updateFile, deleteFile, updateReviewStatus } from '../services/projectsService';
import { FileCard } from './FileCard';
import { FileInfoPanel } from './FileInfoPanel';
import { LinkVideoModal } from './LinkVideoModal';
import { ProjectShareModal } from './ProjectShareModal';
import { ProjectFileContextMenu } from './ProjectFileContextMenu';
import { ProjectCollectModal } from './ProjectCollectModal';

type SortField = 'updated_at' | 'filename' | 'file_size_bytes';
type FilterType = 'all' | 'video' | 'image' | 'document' | 'audio';
type StatusFilter = 'all' | 'pending_review' | 'in_review' | 'feedback_collected' | 'approved' | 'no_status';

interface ProjectFilesViewProps {
  project: Project;
  onBack: () => void;
  onFileReview?: (file: ProjectFile) => void;
}

export const ProjectFilesView: React.FC<ProjectFilesViewProps> = ({ project, onBack, onFileReview }) => {
  const { t } = useTranslation();
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<ProjectFile | null>(null);
  const [isLinkModalOpen, setIsLinkModalOpen] = useState(false);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [sortBy, setSortBy] = useState<SortField>('updated_at');
  const [filterType, setFilterType] = useState<FilterType>('all');
  const [uploadMenuOpen, setUploadMenuOpen] = useState(false);
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
  const [folders, setFolders] = useState<ProjectFolder[]>([]);
  const [folderChain, setFolderChain] = useState<ProjectFolder[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [lastSelectedId, setLastSelectedId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [shareFile, setShareFile] = useState<ProjectFile | null>(null);
  const [contextMenu, setContextMenu] = useState<{ file: ProjectFile; x: number; y: number } | null>(null);
  const [isCollectOpen, setIsCollectOpen] = useState(false);
  const [renameFile, setRenameFile] = useState<ProjectFile | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const uploadMenuRef = useRef<HTMLDivElement>(null);

  // Close upload menu on outside click
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (uploadMenuRef.current && !uploadMenuRef.current.contains(e.target as Node)) {
        setUploadMenuOpen(false);
      }
    };
    if (uploadMenuOpen) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [uploadMenuOpen]);

  useEffect(() => {
    loadContent();
  }, [project.id, currentFolderId]);

  const loadContent = async () => {
    setIsLoading(true);
    try {
      const [filesData, foldersData] = await Promise.all([
        fetchProjectFiles(project.id, false, currentFolderId),
        fetchProjectFolders(project.id, currentFolderId),
      ]);
      setFiles(filesData);
      setFolders(foldersData);
    } catch (err) {
      console.error('Failed to load content:', err);
    } finally {
      setIsLoading(false);
    }
  };

  // Build breadcrumb chain when navigating into a folder
  const navigateToFolder = (folderId: string | null, folder?: ProjectFolder) => {
    if (folderId === null) {
      setFolderChain([]);
    } else if (folder) {
      const existingIdx = folderChain.findIndex(f => f.id === folderId);
      if (existingIdx >= 0) {
        setFolderChain(folderChain.slice(0, existingIdx + 1));
      } else {
        setFolderChain([...folderChain, folder]);
      }
    }
    setCurrentFolderId(folderId);
  };

  const handleCreateFolder = async () => {
    try {
      await createProjectFolder(project.id, 'New Folder', currentFolderId);
      await loadContent();
    } catch (err) {
      console.error('Failed to create folder:', err);
    }
  };

  const toggleSelect = (id: string, e: React.MouseEvent) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (e.shiftKey && lastSelectedId) {
        const allIds = filteredAndSorted.map(f => f.id);
        const start = allIds.indexOf(lastSelectedId);
        const end = allIds.indexOf(id);
        if (start >= 0 && end >= 0) {
          const range = allIds.slice(Math.min(start, end), Math.max(start, end) + 1);
          range.forEach(rid => next.add(rid));
        }
      } else if (e.metaKey || e.ctrlKey) {
        next.has(id) ? next.delete(id) : next.add(id);
      } else {
        return new Set(next.has(id) ? [] : [id]);
      }
      return next;
    });
    setLastSelectedId(id);
  };

  const handleBatchTrash = async () => {
    if (!window.confirm(t('projects.batch.confirmTrash', `Move ${selectedIds.size} files to trash?`))) return;
    try {
      for (const fileId of selectedIds) {
        await updateFile(project.id, fileId, { is_trashed: true });
      }
      setSelectedIds(new Set());
      await loadContent();
    } catch (err) {
      console.error('Failed to batch trash files:', err);
    }
  };

  const handleFileClick = (file: ProjectFile) => {
    // Video files go to the review page; others open the info panel
    if (onFileReview && file.file_type === 'video') {
      onFileReview(file);
    } else {
      setSelectedFile(file);
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const filesList = e.target.files;
    if (!filesList) return;

    setIsUploading(true);
    try {
      for (const file of Array.from(filesList)) {
        await uploadFile(project.id, file);
      }
      await loadContent();
    } catch (err) {
      console.error('Failed to upload file:', err);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
      if (folderInputRef.current) folderInputRef.current.value = '';
    }
  };

  // Determine file category from mime_type / file_type
  const getFileCategory = (file: ProjectFile): FilterType => {
    const ft = file.file_type?.toLowerCase() || '';
    const mt = file.mime_type?.toLowerCase() || '';
    if (ft === 'video' || mt.startsWith('video/')) return 'video';
    if (ft === 'image' || mt.startsWith('image/')) return 'image';
    if (ft === 'audio' || mt.startsWith('audio/')) return 'audio';
    return 'document';
  };

  const filteredAndSorted = useMemo(() => {
    let result = files.filter(f => !f.is_trashed);
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(f => (f.filename || '').toLowerCase().includes(q));
    }
    if (filterType !== 'all') {
      result = result.filter(f => getFileCategory(f) === filterType);
    }
    if (statusFilter !== 'all') {
      if (statusFilter === 'no_status') {
        result = result.filter(f => !f.review_status);
      } else {
        result = result.filter(f => f.review_status === statusFilter);
      }
    }
    result.sort((a, b) => {
      if (sortBy === 'filename') return (a.filename || '').localeCompare(b.filename || '');
      if (sortBy === 'file_size_bytes') return (b.file_size_bytes || 0) - (a.file_size_bytes || 0);
      return new Date(b.updated_at || b.created_at).getTime() - new Date(a.updated_at || a.created_at).getTime();
    });
    return result;
  }, [files, filterType, statusFilter, sortBy, searchQuery]);

  const handleFileContextMenu = (e: React.MouseEvent, file: ProjectFile) => {
    e.preventDefault();
    setContextMenu({ file, x: e.clientX, y: e.clientY });
  };

  const handleDownload = (file: ProjectFile) => {
    if (file.file_path) {
      window.open(file.file_path, '_blank');
    }
  };

  const handleRenameStart = (file: ProjectFile) => {
    setRenameFile(file);
    setRenameValue(file.filename || '');
  };

  const handleRenameSubmit = async () => {
    if (!renameFile || !renameValue.trim()) return;
    try {
      await updateFile(project.id, renameFile.id, { filename: renameValue.trim() });
      await loadContent();
    } catch { /* silent */ }
    setRenameFile(null);
  };

  const handleSetStatus = async (file: ProjectFile, status: ReviewStatus | null) => {
    try {
      await updateReviewStatus(project.id, file.id, status);
      await loadContent();
    } catch { /* silent */ }
  };

  const handleDeleteFile = async (file: ProjectFile) => {
    try {
      await deleteFile(project.id, file.id);
      await loadContent();
    } catch { /* silent */ }
  };

  return (
    <div className="flex h-full">
      {/* Main content */}
      <div className="flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-3">
            <button
              onClick={onBack}
              className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-xl transition-colors"
            >
              <ArrowLeft size={20} />
            </button>
            <h1 className="text-2xl font-bold text-white">{project.name}</h1>
            {project.announcement && (
              <span className="text-xs text-zinc-500 bg-zinc-800 px-2 py-1 rounded-lg max-w-[200px] truncate">
                {project.announcement}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            {/* Member avatars placeholder */}
            <div className="flex items-center -space-x-2">
              <div className="w-7 h-7 rounded-full bg-indigo-500/30 flex items-center justify-center text-[10px] text-indigo-300 border-2 border-zinc-900">
                <Users size={12} />
              </div>
              <button className="w-7 h-7 rounded-full bg-zinc-800 border-2 border-zinc-900 flex items-center justify-center text-zinc-500 hover:text-zinc-300 transition-colors">
                <Plus size={12} />
              </button>
            </div>

            {/* Search */}
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
              <input
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder={t('projects.header.searchFiles', 'Search files...')}
                className="w-40 pl-8 pr-3 py-1.5 text-xs bg-zinc-800/60 border border-zinc-700/50 rounded-lg text-zinc-200 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:w-56 transition-all"
              />
            </div>

            {/* Collect */}
            <button
              onClick={() => setIsCollectOpen(true)}
              className="flex items-center gap-2 px-3 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg font-medium transition-colors text-sm border border-zinc-700"
            >
              <Inbox size={14} />
              {t('projects.collect.title', 'Collect')}
            </button>

            {/* Link Video */}
            <button
              onClick={() => setIsLinkModalOpen(true)}
              className="flex items-center gap-2 px-3 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition-colors text-sm"
            >
              <Link size={14} />
              {t('mediatrack.linkVideo')}
            </button>
          </div>
        </div>

        {/* Toolbar */}
        <div className="flex items-center justify-between mb-4">
          <span className="text-xs text-zinc-500">
            {filteredAndSorted.length} {t('projects.toolbar.items', 'items')}
          </span>
          <div className="flex items-center gap-2">
            {/* Sort */}
            <select
              value={sortBy}
              onChange={e => setSortBy(e.target.value as SortField)}
              className="text-xs bg-zinc-800 border border-zinc-700/50 rounded-lg px-2 py-1.5 text-zinc-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value="updated_at">{t('projects.toolbar.sortUpdated', 'Updated')}</option>
              <option value="filename">{t('projects.toolbar.sortName', 'Name')}</option>
              <option value="file_size_bytes">{t('projects.toolbar.sortSize', 'Size')}</option>
            </select>

            {/* Filter */}
            <select
              value={filterType}
              onChange={e => setFilterType(e.target.value as FilterType)}
              className="text-xs bg-zinc-800 border border-zinc-700/50 rounded-lg px-2 py-1.5 text-zinc-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value="all">{t('projects.toolbar.filterAll', 'All Types')}</option>
              <option value="video">{t('projects.toolbar.filterVideo', 'Video')}</option>
              <option value="image">{t('projects.toolbar.filterImage', 'Image')}</option>
              <option value="audio">{t('projects.toolbar.filterAudio', 'Audio')}</option>
              <option value="document">{t('projects.toolbar.filterDocument', 'Document')}</option>
            </select>

            {/* Status filter */}
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as StatusFilter)}
              className="text-xs bg-zinc-800 border border-zinc-700/50 rounded-lg px-2 py-1.5 text-zinc-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value="all">{t('projects.toolbar.statusAll', 'All Status')}</option>
              <option value="pending_review">{t('projects.toolbar.statusPending', 'Pending Review')}</option>
              <option value="in_review">{t('projects.toolbar.statusInReview', 'In Review')}</option>
              <option value="feedback_collected">{t('projects.toolbar.statusFeedback', 'Feedback')}</option>
              <option value="approved">{t('projects.toolbar.statusApproved', 'Approved')}</option>
              <option value="no_status">{t('projects.toolbar.statusNone', 'No Status')}</option>
            </select>

            {/* View toggle */}
            <div className="flex bg-zinc-800 rounded-lg p-0.5">
              <button
                onClick={() => setViewMode('grid')}
                className={`p-1.5 rounded-md transition-colors ${
                  viewMode === 'grid' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <LayoutGrid size={14} />
              </button>
              <button
                onClick={() => setViewMode('list')}
                className={`p-1.5 rounded-md transition-colors ${
                  viewMode === 'list' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <LayoutList size={14} />
              </button>
            </div>

            {/* Upload dropdown */}
            <div className="relative" ref={uploadMenuRef}>
              <button
                onClick={() => setUploadMenuOpen(!uploadMenuOpen)}
                disabled={isUploading}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 hover:bg-green-500 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              >
                {isUploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                {t('mediatrack.upload')}
                <ChevronDown size={12} />
              </button>
              {uploadMenuOpen && (
                <div className="absolute right-0 top-full mt-1 w-44 bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl z-50 py-1">
                  <button
                    onClick={() => { fileInputRef.current?.click(); setUploadMenuOpen(false); }}
                    className="w-full text-left px-3 py-2 text-sm text-zinc-200 hover:bg-zinc-700 transition-colors"
                  >
                    {t('projects.toolbar.uploadFiles', 'Upload Files')}
                  </button>
                  <button
                    onClick={() => { folderInputRef.current?.click(); setUploadMenuOpen(false); }}
                    className="w-full text-left px-3 py-2 text-sm text-zinc-200 hover:bg-zinc-700 transition-colors"
                  >
                    {t('projects.toolbar.uploadFolder', 'Upload Folder')}
                  </button>
                </div>
              )}
            </div>

            {/* New Folder */}
            <button
              onClick={handleCreateFolder}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-sm font-medium transition-colors"
            >
              <Plus size={14} />
              {t('projects.toolbar.newFolder', 'New Folder')}
            </button>

            {/* Hidden inputs */}
            <input ref={fileInputRef} type="file" multiple onChange={handleUpload} className="hidden" />
            <input ref={folderInputRef} type="file" {...{ webkitdirectory: '', directory: '' } as any} multiple onChange={handleUpload} className="hidden" />
          </div>
        </div>

        {/* Breadcrumb */}
        {folderChain.length > 0 && (
          <nav className="flex items-center gap-1 text-sm mb-3">
            <button
              onClick={() => navigateToFolder(null)}
              className="text-zinc-400 hover:text-white transition-colors"
            >
              {project.name}
            </button>
            {folderChain.map((f) => (
              <React.Fragment key={f.id}>
                <ChevronRight size={14} className="text-zinc-600" />
                <button
                  onClick={() => navigateToFolder(f.id, f)}
                  className={`transition-colors ${
                    f.id === currentFolderId ? 'text-white' : 'text-zinc-400 hover:text-white'
                  }`}
                >
                  {f.name}
                </button>
              </React.Fragment>
            ))}
          </nav>
        )}

        {/* Loading */}
        {isLoading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-8 h-8 text-indigo-400 animate-spin" />
          </div>
        )}

        {/* Empty state */}
        {!isLoading && folders.length === 0 && filteredAndSorted.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="p-4 bg-zinc-800 rounded-2xl mb-4">
              <FolderOpen size={40} className="text-zinc-500" />
            </div>
            <h3 className="text-lg font-medium text-zinc-300 mb-2">{t('mediatrack.noFiles')}</h3>
            <p className="text-sm text-zinc-500 mb-4">{t('mediatrack.uploadOrLink')}</p>
            <div className="flex gap-3">
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex items-center gap-2 px-4 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-xl font-medium transition-colors text-sm"
              >
                <Upload size={16} />
                {t('mediatrack.upload')}
              </button>
              <button
                onClick={() => setIsLinkModalOpen(true)}
                className="flex items-center gap-2 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-medium transition-colors text-sm"
              >
                <Link size={16} />
                {t('mediatrack.linkVideo')}
              </button>
            </div>
          </div>
        )}

        {/* Grid view */}
        {!isLoading && (folders.length > 0 || filteredAndSorted.length > 0) && viewMode === 'grid' && (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {folders.map(folder => (
              <div
                key={folder.id}
                onClick={() => navigateToFolder(folder.id, folder)}
                className="group bg-zinc-800/50 hover:bg-zinc-800 border border-zinc-700/50 rounded-xl p-4 cursor-pointer transition-colors flex flex-col items-center gap-2"
              >
                <FolderIcon size={44} className="text-amber-400" />
                <p className="text-sm text-zinc-200 truncate w-full text-center">{folder.name}</p>
              </div>
            ))}
            {filteredAndSorted.map(file => (
              <div key={file.id} onContextMenu={(e) => handleFileContextMenu(e, file)}>
                <FileCard
                  file={file}
                  onClick={() => handleFileClick(file)}
                  viewMode="grid"
                  isSelected={selectedIds.has(file.id)}
                  onToggleSelect={(e) => toggleSelect(file.id, e)}
                />
              </div>
            ))}
          </div>
        )}

        {/* List view */}
        {!isLoading && (folders.length > 0 || filteredAndSorted.length > 0) && viewMode === 'list' && (
          <div className="space-y-2">
            {folders.map(folder => (
              <div
                key={folder.id}
                onClick={() => navigateToFolder(folder.id, folder)}
                className="flex items-center gap-3 px-4 py-3 bg-zinc-800/50 hover:bg-zinc-800 border border-zinc-700/50 rounded-xl cursor-pointer transition-colors"
              >
                <FolderIcon size={20} className="text-amber-400 shrink-0" />
                <span className="text-sm text-zinc-200 truncate">{folder.name}</span>
              </div>
            ))}
            {filteredAndSorted.map(file => (
              <div key={file.id} onContextMenu={(e) => handleFileContextMenu(e, file)}>
                <FileCard
                  file={file}
                  onClick={() => handleFileClick(file)}
                  viewMode="list"
                  isSelected={selectedIds.has(file.id)}
                  onToggleSelect={(e) => toggleSelect(file.id, e)}
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Floating batch action bar */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-zinc-900 border border-zinc-700 rounded-xl px-5 py-3 flex items-center gap-4 shadow-2xl">
          <span className="text-sm text-zinc-300">
            {selectedIds.size} {t('projects.batch.selected', 'selected')}
          </span>
          <button
            onClick={handleBatchTrash}
            className="text-sm text-red-400 hover:text-red-300 px-3 py-1.5 hover:bg-red-500/10 rounded-lg transition-colors"
          >
            {t('projects.batch.trash', 'Trash')}
          </button>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="text-sm text-zinc-500 hover:text-zinc-300 px-3 py-1.5 transition-colors"
          >
            {t('projects.batch.cancel', 'Cancel')}
          </button>
        </div>
      )}

      {/* File Info Panel */}
      {selectedFile && (
        <FileInfoPanel
          file={selectedFile}
          onClose={() => setSelectedFile(null)}
        />
      )}

      {/* Link Video Modal */}
      <LinkVideoModal
        isOpen={isLinkModalOpen}
        onClose={() => setIsLinkModalOpen(false)}
        projectId={project.id}
        onLinked={() => {
          setIsLinkModalOpen(false);
          loadContent();
        }}
      />

      {/* Share Modal */}
      {shareFile && (
        <ProjectShareModal
          file={shareFile}
          projectId={project.id}
          isOpen={!!shareFile}
          onClose={() => setShareFile(null)}
          onCreated={() => {}}
        />
      )}

      {/* File Context Menu */}
      {contextMenu && (
        <ProjectFileContextMenu
          file={contextMenu.file}
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          onDownload={handleDownload}
          onRename={handleRenameStart}
          onMove={() => {}}
          onSetStatus={handleSetStatus}
          onVersionHistory={(f) => setSelectedFile(f)}
          onFileInfo={(f) => setSelectedFile(f)}
          onShare={(f) => setShareFile(f)}
          onDelete={handleDeleteFile}
        />
      )}

      {/* Collect Modal */}
      <ProjectCollectModal
        projectId={project.id}
        isOpen={isCollectOpen}
        onClose={() => setIsCollectOpen(false)}
      />

      {/* Rename Dialog */}
      {renameFile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setRenameFile(null)}>
          <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-sm mx-4 p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-medium text-zinc-200 mb-3">{t('projects.fileMenu.rename', 'Rename')}</h3>
            <input
              autoFocus
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleRenameSubmit(); if (e.key === 'Escape') setRenameFile(null); }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500"
            />
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setRenameFile(null)} className="px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200 transition-colors">
                {t('common.cancel', 'Cancel')}
              </button>
              <button onClick={handleRenameSubmit} className="px-3 py-1.5 text-sm bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-colors">
                {t('common.save', 'Save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
