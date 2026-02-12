import React, { useState, useRef, useEffect, useCallback } from 'react';
import { ArrowLeft, Share2, ChevronDown, MessageSquare, Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ProjectFile, FileVersion, ReviewStatus } from '../types';
import { fetchFileVersions, getFileInfo, updateReviewStatus } from '../services/projectsService';
import { VideoPlayer } from './VideoPlayer';
import { ReviewCommentsPanel } from './ReviewCommentsPanel';
import { FileInfoPanel } from './FileInfoPanel';
import { VersionManagerModal } from './VersionManagerModal';
import { ReviewStatusDropdown } from './ReviewStatusDropdown';

interface VideoReviewPageProps {
  projectId: string;
  file: ProjectFile;
  onBack: () => void;
  currentUserId: string;
}

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

export const VideoReviewPage: React.FC<VideoReviewPageProps> = ({
  projectId,
  file: initialFile,
  onBack,
  currentUserId,
}) => {
  const { t } = useTranslation();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [file, setFile] = useState<ProjectFile>(initialFile);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [versions, setVersions] = useState<FileVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<FileVersion | null>(null);
  const [activeTab, setActiveTab] = useState<'comments' | 'info'>('comments');
  const [isVersionModalOpen, setIsVersionModalOpen] = useState(false);
  const [isVersionDropdownOpen, setIsVersionDropdownOpen] = useState(false);
  const versionDropdownRef = useRef<HTMLDivElement>(null);

  // Load versions on mount
  useEffect(() => {
    loadVersions();
  }, [file.id]);

  // Close version dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (versionDropdownRef.current && !versionDropdownRef.current.contains(e.target as Node)) {
        setIsVersionDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const loadVersions = async () => {
    try {
      const data = await fetchFileVersions(projectId, file.id);
      setVersions(data);
      // Set selected version to the current one
      const current = data.find(v => v.version_number === file.current_version);
      if (current) setSelectedVersion(current);
    } catch (err) {
      console.error('Failed to load versions:', err);
    }
  };

  const refreshFile = useCallback(async () => {
    try {
      const updated = await getFileInfo(projectId, file.id);
      setFile(updated);
    } catch (err) {
      console.error('Failed to refresh file:', err);
    }
  }, [projectId, file.id]);

  const handleSeekTo = useCallback((seconds: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = seconds;
    }
  }, []);

  const handleStatusChange = async (status: ReviewStatus | null) => {
    try {
      await updateReviewStatus(projectId, file.id, status);
      setFile(prev => ({ ...prev, review_status: status }));
    } catch (err) {
      console.error('Failed to update review status:', err);
    }
  };

  const handleVersionSelect = (version: FileVersion) => {
    setSelectedVersion(version);
    setIsVersionModalOpen(false);
  };

  const handleVersionUploaded = async () => {
    await loadVersions();
    await refreshFile();
  };

  // Determine video source URL
  const videoSrc = selectedVersion?.file_path
    ? `${getApiUrl()}/media/${selectedVersion.file_path}`
    : file.file_path
      ? `${getApiUrl()}/media/${file.file_path}`
      : '';

  const videoMime = selectedVersion?.mime_type || file.mime_type || undefined;

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800 bg-zinc-900/80 backdrop-blur-sm flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={onBack}
            className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-xl transition-colors flex-shrink-0"
          >
            <ArrowLeft size={20} />
          </button>
          <h1 className="text-lg font-semibold text-white truncate">
            {file.filename}
          </h1>

          {/* Version badge dropdown */}
          <div ref={versionDropdownRef} className="relative flex-shrink-0">
            <button
              onClick={() => setIsVersionDropdownOpen(!isVersionDropdownOpen)}
              className="flex items-center gap-1 px-2.5 py-1 bg-indigo-500/20 text-indigo-300 text-xs font-medium rounded-full hover:bg-indigo-500/30 transition-colors"
            >
              V{selectedVersion?.version_number || file.current_version}
              <ChevronDown size={12} />
            </button>
            {isVersionDropdownOpen && versions.length > 0 && (
              <div className="absolute top-full left-0 mt-1 w-48 bg-zinc-800 border border-zinc-700 rounded-xl shadow-xl z-50 py-1 animate-in fade-in slide-in-from-top-2 duration-150">
                {versions.map(v => (
                  <button
                    key={v.id}
                    onClick={() => {
                      handleVersionSelect(v);
                      setIsVersionDropdownOpen(false);
                    }}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-sm transition-colors ${
                      selectedVersion?.id === v.id
                        ? 'bg-indigo-500/20 text-indigo-300'
                        : 'text-zinc-300 hover:bg-zinc-700'
                    }`}
                  >
                    <span className="font-medium">V{v.version_number}</span>
                    <span className="text-xs text-zinc-500 truncate">{v.filename}</span>
                    {v.version_number === file.current_version && (
                      <span className="ml-auto text-[10px] bg-indigo-500/20 text-indigo-300 px-1.5 py-0.5 rounded-full">
                        {t('mediatrack.review.currentVersion')}
                      </span>
                    )}
                  </button>
                ))}
                <div className="border-t border-zinc-700 mt-1 pt-1">
                  <button
                    onClick={() => {
                      setIsVersionDropdownOpen(false);
                      setIsVersionModalOpen(true);
                    }}
                    className="w-full text-left px-3 py-2 text-sm text-zinc-400 hover:text-white hover:bg-zinc-700 transition-colors"
                  >
                    {t('mediatrack.review.manageVersions')}...
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <ReviewStatusDropdown
            currentStatus={file.review_status}
            onStatusChange={handleStatusChange}
          />
          <button
            className="flex items-center gap-2 px-3 py-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-xl transition-colors text-sm"
          >
            <Share2 size={16} />
            {t('mediatrack.review.share')}
          </button>
        </div>
      </div>

      {/* Main content: video left, panel right */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left: Video player */}
        <div className="flex-1 min-w-0 flex flex-col bg-black">
          <div className="flex-1 min-h-0 flex items-center justify-center">
            {videoSrc ? (
              <VideoPlayer
                src={videoSrc}
                mimeType={videoMime}
                onTimeUpdate={setCurrentTime}
                onDurationChange={setDuration}
                playerRef={videoRef}
              />
            ) : (
              <div className="text-zinc-500 text-sm">No video source available</div>
            )}
          </div>
        </div>

        {/* Right: Comments/Info panel */}
        <div className="w-[400px] flex-shrink-0 border-l border-zinc-800 flex flex-col bg-zinc-900">
          {/* Tab headers */}
          <div className="flex border-b border-zinc-800 flex-shrink-0">
            <button
              onClick={() => setActiveTab('comments')}
              className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 text-sm font-medium transition-colors ${
                activeTab === 'comments'
                  ? 'text-white border-b-2 border-indigo-500'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <MessageSquare size={16} />
              {t('mediatrack.review.comments')}
            </button>
            <button
              onClick={() => setActiveTab('info')}
              className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 text-sm font-medium transition-colors ${
                activeTab === 'info'
                  ? 'text-white border-b-2 border-indigo-500'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <Info size={16} />
              {t('mediatrack.review.fileInfo')}
            </button>
          </div>

          {/* Tab content */}
          <div className="flex-1 min-h-0 overflow-y-auto">
            {activeTab === 'comments' ? (
              <ReviewCommentsPanel
                projectId={projectId}
                fileId={file.id}
                versionId={selectedVersion?.id}
                currentTime={currentTime}
                currentUserId={currentUserId}
                onSeekTo={handleSeekTo}
                onCommentAdded={() => {}}
              />
            ) : (
              <FileInfoPanel
                file={file}
                onClose={() => setActiveTab('comments')}
              />
            )}
          </div>
        </div>
      </div>

      {/* Version Manager Modal */}
      <VersionManagerModal
        isOpen={isVersionModalOpen}
        onClose={() => setIsVersionModalOpen(false)}
        projectId={projectId}
        fileId={file.id}
        currentVersionNumber={file.current_version}
        onVersionSelect={handleVersionSelect}
        onVersionUploaded={handleVersionUploaded}
      />
    </div>
  );
};
