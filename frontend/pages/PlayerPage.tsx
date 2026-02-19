import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useSearchParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Loader2, FileQuestion, UserRound } from 'lucide-react';
import { Video } from '../types';
import { VideoPlayer } from '../components/VideoPlayer';
import { VideoDetailPanel } from '../components/VideoDetailPanel';
import { fetchVideoByDisplayId, updateItem, deleteItem } from '../services/dataService';
import { getVideoUrl } from '../utils/awemeType';

const MIN_PANEL_WIDTH = 380;
const MAX_PANEL_WIDTH = 800;
const DEFAULT_PANEL_WIDTH = 560;

export function PlayerPage() {
  const { displayId, teamId } = useParams<{ displayId: string; teamId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const from = searchParams.get('from');

  const [video, setVideo] = useState<Video | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const playerRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_PANEL_WIDTH);
  const isDragging = useRef(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

  const handleTimeUpdate = useCallback((seconds: number) => {
    setCurrentTime(seconds);
  }, []);

  const handleDurationChange = useCallback((seconds: number) => {
    setDuration(seconds);
  }, []);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;
    dragStartX.current = e.clientX;
    dragStartWidth.current = panelWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const handleMouseMove = (ev: MouseEvent) => {
      if (!isDragging.current) return;
      const delta = dragStartX.current - ev.clientX;
      const newWidth = Math.min(MAX_PANEL_WIDTH, Math.max(MIN_PANEL_WIDTH, dragStartWidth.current + delta));
      setPanelWidth(newWidth);
    };

    const handleMouseUp = () => {
      isDragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  }, [panelWidth]);

  useEffect(() => {
    if (!displayId) return;

    setIsLoading(true);
    setNotFound(false);

    fetchVideoByDisplayId(displayId).then((data) => {
      if (data) {
        setVideo(data);
      } else {
        setNotFound(true);
      }
      setIsLoading(false);
    });
  }, [displayId]);

  const handleBack = () => {
    if (from === 'downloads' && teamId) {
      navigate(`/t/${teamId}/resources/downloads`);
    } else {
      navigate(-1);
    }
  };

  const handleUpdate = async (id: string, updates: Partial<Video>) => {
    const updated = await updateItem(id, updates);
    setVideo((prev) => (prev ? { ...prev, ...updated } : prev));
  };

  const handleDelete = async (id: string, deleteFiles: boolean) => {
    await deleteItem(id, deleteFiles);
    handleBack();
  };

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[400px]">
        <Loader2 size={32} className="animate-spin text-zinc-500 mb-3" />
        <p className="text-zinc-500 text-sm">Loading...</p>
      </div>
    );
  }

  if (notFound || !video) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[400px] text-center">
        <FileQuestion size={48} className="text-zinc-600 mb-4" />
        <p className="text-zinc-400 text-lg font-medium mb-2">Video not found</p>
        <p className="text-zinc-500 text-sm mb-6">The video you're looking for doesn't exist or has been removed.</p>
        <button
          onClick={handleBack}
          className="flex items-center gap-2 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors"
        >
          <ArrowLeft size={16} />
          Go Back
        </button>
      </div>
    );
  }

  return (
    <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 h-full flex flex-col">
      <div className="flex flex-col h-full p-4 md:p-0">
        <div className="flex items-center gap-3 mb-4 shrink-0">
          <button
            onClick={handleBack}
            className="p-2 -ml-2 rounded-full hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors"
          >
            <ArrowLeft size={24} />
          </button>
          <h2 className="text-xl font-bold text-white truncate">{video.title || video.description || 'Media Player'}</h2>
        </div>
        <div className="flex-1 min-h-0 flex">
          {/* Video Player — main area */}
          <div className="flex-1 min-w-0 relative">
            <VideoPlayer
              src={getVideoUrl(video) || ''}
              playerRef={playerRef}
              onTimeUpdate={handleTimeUpdate}
              onDurationChange={handleDurationChange}
            />
            {video.author && (
              <div className="absolute bottom-12 left-4 flex items-center gap-2 text-white/80 text-sm pointer-events-none">
                <UserRound size={18} className="opacity-70" />
                <span>@{video.author}</span>
              </div>
            )}
          </div>
          {/* Resize handle */}
          <div
            onMouseDown={handleResizeStart}
            className="w-1 shrink-0 cursor-col-resize group relative mx-1.5"
          >
            <div className="absolute inset-y-0 -left-1 -right-1 group-hover:bg-blue-500/30 transition-colors rounded" />
          </div>
          {/* Detail Panel — right sidebar */}
          <div
            style={{ width: panelWidth }}
            className="shrink-0 overflow-y-auto custom-scrollbar"
          >
            <VideoDetailPanel
              video={video}
              onClose={handleBack}
              onUpdate={handleUpdate}
              onDelete={handleDelete}
              hidePreview
            />
          </div>
        </div>
      </div>
    </div>
  );
}
