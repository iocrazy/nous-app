import React, { useState, useRef, useEffect, useCallback } from 'react';
import { ArrowLeft, Share2, ChevronDown, MessageSquare, Info, PenTool, Columns2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ProjectFile, FileVersion, ReviewStatus, DrawingData } from '../types';
import { fetchFileVersions, getFileInfo, updateReviewStatus } from '../services/projectsService';
import { VideoPlayer } from './VideoPlayer';
import { VersionCompareView } from './VersionCompareView';
import { ReviewCommentsPanel } from './ReviewCommentsPanel';
import { FileInfoPanel } from './FileInfoPanel';
import { ProjectVersionModal } from './ProjectVersionModal';
import { ReviewStatusDropdown } from './ReviewStatusDropdown';
import { AnnotationCanvas, AnnotationCanvasHandle } from './AnnotationCanvas';
import { AnnotationToolbar } from './AnnotationToolbar';

interface VideoReviewPageProps {
  projectId: string;
  file: ProjectFile;
  onBack: () => void;
  currentUserId: string;
}

import { getApiUrl } from '../utils/apiConfig';

const getVersionVideoSrc = (version: FileVersion): string => {
  if (!version.file_path) return '';
  return `${getApiUrl()}/media/${version.file_path}`;
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

  // Compare mode state
  const [compareMode, setCompareMode] = useState(false);
  const [compareVersionId, setCompareVersionId] = useState<string | null>(null);
  const [isCompareDropdownOpen, setIsCompareDropdownOpen] = useState(false);
  const compareDropdownRef = useRef<HTMLDivElement>(null);

  // Annotation state
  const [isAnnotating, setIsAnnotating] = useState(false);
  const [annotationTool, setAnnotationTool] = useState<'pen' | 'arrow' | 'rect' | 'circle' | 'text'>('pen');
  const [annotationColor, setAnnotationColor] = useState('#ef4444');
  const [annotationStrokeWidth, setAnnotationStrokeWidth] = useState(4);
  const [currentDrawingData, setCurrentDrawingData] = useState<DrawingData | null>(null);
  const [viewingDrawingData, setViewingDrawingData] = useState<DrawingData | null>(null);
  const annotationCanvasRef = useRef<AnnotationCanvasHandle>(null);
  const videoContainerRef = useRef<HTMLDivElement>(null);

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
      if (compareDropdownRef.current && !compareDropdownRef.current.contains(e.target as Node)) {
        setIsCompareDropdownOpen(false);
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
    // Exit compare mode when switching primary version
    if (compareMode) {
      setCompareMode(false);
      setCompareVersionId(null);
    }
  };

  const handleVersionUploaded = async () => {
    await loadVersions();
    await refreshFile();
  };

  // Compare mode handlers
  const handleEnterCompareMode = useCallback((compareVersionId: string) => {
    // Exit annotation mode when entering compare
    setIsAnnotating(false);
    setViewingDrawingData(null);
    setCompareVersionId(compareVersionId);
    setCompareMode(true);
    setIsCompareDropdownOpen(false);
  }, []);

  const handleExitCompareMode = useCallback(() => {
    setCompareMode(false);
    setCompareVersionId(null);
  }, []);

  // Get the compare version object
  const compareVersion = compareVersionId ? versions.find(v => v.id === compareVersionId) : null;

  // Versions available for comparison (exclude currently selected)
  const comparableVersions = versions.filter(v => v.id !== selectedVersion?.id);

  // Annotation handlers
  const handleAnnotateToggle = useCallback(() => {
    if (isAnnotating) {
      // Closing annotation mode
      setIsAnnotating(false);
      setViewingDrawingData(null);
    } else {
      // Pause video when entering annotation mode
      if (videoRef.current && !videoRef.current.paused) {
        videoRef.current.pause();
      }
      setIsAnnotating(true);
      setCurrentDrawingData(null);
      setViewingDrawingData(null);
    }
  }, [isAnnotating]);

  const handleAnnotationClose = useCallback(() => {
    setIsAnnotating(false);
    setViewingDrawingData(null);
  }, []);

  const handleDrawingChange = useCallback((data: DrawingData) => {
    setCurrentDrawingData(data);
  }, []);

  const handleAnnotationUndo = useCallback(() => {
    annotationCanvasRef.current?.undo();
  }, []);

  const handleAnnotationClear = useCallback(() => {
    annotationCanvasRef.current?.clear();
    setCurrentDrawingData(null);
  }, []);

  const handleViewAnnotation = useCallback((drawingData: DrawingData) => {
    setViewingDrawingData(drawingData);
    setIsAnnotating(false);
  }, []);

  const handleCloseViewAnnotation = useCallback(() => {
    setViewingDrawingData(null);
  }, []);

  // Determine video source URL
  const videoSrc = selectedVersion?.file_path
    ? `${getApiUrl()}/media/${selectedVersion.file_path}`
    : file.file_path
      ? `${getApiUrl()}/media/${file.file_path}`
      : '';

  const videoMime = selectedVersion?.mime_type || file.mime_type || undefined;
  const videoFps = selectedVersion?.fps || file.fps || 30;

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

          {/* Compare button / dropdown */}
          {versions.length >= 2 && (
            <div ref={compareDropdownRef} className="relative flex-shrink-0">
              {compareMode ? (
                <button
                  onClick={handleExitCompareMode}
                  className="flex items-center gap-1.5 px-2.5 py-1 bg-amber-500/20 text-amber-300 text-xs font-medium rounded-full hover:bg-amber-500/30 transition-colors"
                >
                  <X size={12} />
                  {t('mediatrack.review.exitCompare')}
                </button>
              ) : (
                <button
                  onClick={() => setIsCompareDropdownOpen(!isCompareDropdownOpen)}
                  className="flex items-center gap-1.5 px-2.5 py-1 bg-zinc-700/50 text-zinc-300 text-xs font-medium rounded-full hover:bg-zinc-700 transition-colors"
                >
                  <Columns2 size={12} />
                  {t('mediatrack.review.compareVersions')}
                </button>
              )}
              {isCompareDropdownOpen && !compareMode && comparableVersions.length > 0 && (
                <div className="absolute top-full left-0 mt-1 w-56 bg-zinc-800 border border-zinc-700 rounded-xl shadow-xl z-50 py-1 animate-in fade-in slide-in-from-top-2 duration-150">
                  <div className="px-3 py-1.5 text-[10px] text-zinc-500 uppercase tracking-wider font-semibold">
                    {t('mediatrack.review.selectVersionToCompare')}
                  </div>
                  {comparableVersions.map(v => (
                    <button
                      key={v.id}
                      onClick={() => handleEnterCompareMode(v.id)}
                      className="w-full flex items-center gap-2 px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-700 transition-colors"
                    >
                      <span className="font-medium">V{v.version_number}</span>
                      <span className="text-xs text-zinc-500 truncate">{v.filename}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
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
        {/* Left: Video player or Compare view */}
        <div className="flex-1 min-w-0 flex flex-col bg-black">
          {compareMode && selectedVersion && compareVersion ? (
            /* Compare mode: side-by-side */
            <VersionCompareView
              projectId={projectId}
              fileId={file.id}
              versionA={selectedVersion}
              versionB={compareVersion}
              fps={videoFps}
              getVideoSrc={getVersionVideoSrc}
            />
          ) : (
            /* Normal mode: single player */
            <div ref={videoContainerRef} className="flex-1 min-h-0 flex items-center justify-center relative">
              {videoSrc ? (
                <>
                  <VideoPlayer
                    src={videoSrc}
                    mimeType={videoMime}
                    fps={videoFps}
                    onTimeUpdate={setCurrentTime}
                    onDurationChange={setDuration}
                    playerRef={videoRef}
                  />

                  {/* Annotation Canvas overlay (active drawing mode) */}
                  {isAnnotating && (
                    <>
                      <AnnotationToolbar
                        activeTool={annotationTool}
                        activeColor={annotationColor}
                        strokeWidth={annotationStrokeWidth}
                        onToolChange={setAnnotationTool}
                        onColorChange={setAnnotationColor}
                        onStrokeWidthChange={setAnnotationStrokeWidth}
                        onUndo={handleAnnotationUndo}
                        onClear={handleAnnotationClear}
                        onClose={handleAnnotationClose}
                      />
                      <AnnotationCanvas
                        ref={annotationCanvasRef}
                        width={videoRef.current?.videoWidth || 1920}
                        height={videoRef.current?.videoHeight || 1080}
                        isActive={true}
                        tool={annotationTool}
                        color={annotationColor}
                        strokeWidth={annotationStrokeWidth}
                        onDrawingChange={handleDrawingChange}
                        onClose={handleAnnotationClose}
                      />
                    </>
                  )}

                  {/* Viewing existing annotation (read-only) */}
                  {viewingDrawingData && !isAnnotating && (
                    <>
                      <AnnotationCanvas
                        width={viewingDrawingData.width || 1920}
                        height={viewingDrawingData.height || 1080}
                        isActive={false}
                        tool="pen"
                        color="#ef4444"
                        strokeWidth={4}
                        existingDrawing={viewingDrawingData}
                        onDrawingChange={() => {}}
                        onClose={handleCloseViewAnnotation}
                      />
                      <button
                        onClick={handleCloseViewAnnotation}
                        className="absolute top-2 right-2 z-20 px-3 py-1.5 bg-zinc-900/90 backdrop-blur border border-zinc-700 rounded-lg text-xs text-zinc-300 hover:text-white hover:bg-zinc-800 transition-colors"
                      >
                        {t('annotations.done')}
                      </button>
                    </>
                  )}

                  {/* Annotate button - shown when not annotating */}
                  {!isAnnotating && !viewingDrawingData && (
                    <button
                      onClick={handleAnnotateToggle}
                      className="absolute top-3 left-3 z-10 flex items-center gap-1.5 px-3 py-1.5 bg-zinc-900/80 backdrop-blur border border-zinc-700 rounded-lg text-xs font-medium text-zinc-300 hover:text-white hover:bg-zinc-800 transition-colors"
                      title={t('annotations.annotate')}
                    >
                      <PenTool size={14} />
                      {t('annotations.annotate')}
                    </button>
                  )}
                </>
              ) : (
                <div className="text-zinc-500 text-sm">No video source available</div>
              )}
            </div>
          )}
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
                pendingDrawingData={isAnnotating ? currentDrawingData : null}
                onViewAnnotation={handleViewAnnotation}
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
      <ProjectVersionModal
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
