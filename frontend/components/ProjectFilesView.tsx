import React, { useState, useEffect, useRef } from 'react';
import { ArrowLeft, Upload, Link, LayoutGrid, LayoutList, Loader2, FileText, FolderOpen } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Project, ProjectFile } from '../types';
import { fetchProjectFiles, uploadFile } from '../services/projectsService';
import { FileCard } from './FileCard';
import { FileInfoPanel } from './FileInfoPanel';
import { LinkVideoModal } from './LinkVideoModal';

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
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadFiles();
  }, [project.id]);

  const loadFiles = async () => {
    setIsLoading(true);
    try {
      const data = await fetchProjectFiles(project.id);
      setFiles(data);
    } catch (err) {
      console.error('Failed to load files:', err);
    } finally {
      setIsLoading(false);
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
      await loadFiles();
    } catch (err) {
      console.error('Failed to upload file:', err);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  return (
    <div className="flex h-full">
      {/* Main content */}
      <div className="flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <button
              onClick={onBack}
              className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-xl transition-colors"
            >
              <ArrowLeft size={20} />
            </button>
            <div>
              <h1 className="text-2xl font-bold text-white">{project.name}</h1>
              <p className="text-sm text-zinc-400">
                {files.length} {t('mediatrack.files')}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* View toggle */}
            <div className="flex bg-zinc-800 rounded-xl p-1">
              <button
                onClick={() => setViewMode('grid')}
                className={`p-2 rounded-lg transition-colors ${
                  viewMode === 'grid' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <LayoutGrid size={16} />
              </button>
              <button
                onClick={() => setViewMode('list')}
                className={`p-2 rounded-lg transition-colors ${
                  viewMode === 'list' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <LayoutList size={16} />
              </button>
            </div>

            {/* Upload */}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
              className="flex items-center gap-2 px-4 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-xl font-medium transition-colors text-sm disabled:opacity-50"
            >
              {isUploading ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Upload size={16} />
              )}
              {t('mediatrack.upload')}
            </button>

            {/* Link Video */}
            <button
              onClick={() => setIsLinkModalOpen(true)}
              className="flex items-center gap-2 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-medium transition-colors text-sm"
            >
              <Link size={16} />
              {t('mediatrack.linkVideo')}
            </button>

            {/* Hidden file input */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              onChange={handleUpload}
              className="hidden"
            />
          </div>
        </div>

        {/* Loading */}
        {isLoading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-8 h-8 text-indigo-400 animate-spin" />
          </div>
        )}

        {/* Empty state */}
        {!isLoading && files.length === 0 && (
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
        {!isLoading && files.length > 0 && viewMode === 'grid' && (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {files.map(file => (
              <FileCard
                key={file.id}
                file={file}
                onClick={() => handleFileClick(file)}
                viewMode="grid"
              />
            ))}
          </div>
        )}

        {/* List view */}
        {!isLoading && files.length > 0 && viewMode === 'list' && (
          <div className="space-y-2">
            {files.map(file => (
              <FileCard
                key={file.id}
                file={file}
                onClick={() => handleFileClick(file)}
                viewMode="list"
              />
            ))}
          </div>
        )}
      </div>

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
          loadFiles();
        }}
      />
    </div>
  );
};
