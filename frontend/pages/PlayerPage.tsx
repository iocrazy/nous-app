import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useSearchParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Loader2, FileQuestion } from 'lucide-react';
import { Video } from '../types';
import { VideoPlayer } from '../components/VideoPlayer';
import { VideoDetailPanel } from '../components/VideoDetailPanel';
import { fetchVideoByDisplayId, updateItem, deleteItem, getDownloadUrl } from '../services/dataService';
import { useTeamContext } from '../contexts/TeamContext';
import { useLibrary } from '../hooks/useLibrary';

export function PlayerPage() {
  const { displayId, teamId } = useParams<{ displayId: string; teamId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { selectedTeamId } = useTeamContext();
  const from = searchParams.get('from');

  const [video, setVideo] = useState<Video | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const playerRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  const handleTimeUpdate = useCallback((seconds: number) => {
    setCurrentTime(seconds);
  }, []);

  const handleDurationChange = useCallback((seconds: number) => {
    setDuration(seconds);
  }, []);

  const {
    collections,
    selectedVideoCollectionIds,
    handleToggleVideoCollection,
    handleCreateCollection,
  } = useLibrary({ isAuthenticated: true, selectedTeamId });

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
          <h2 className="text-xl font-bold text-white truncate">{video.title || video.desc || 'Media Player'}</h2>
        </div>
        <div className="flex-1 min-h-0 flex gap-4">
          {/* Video Player — main area */}
          <div className="flex-1 min-w-0">
            <VideoPlayer
              src={getDownloadUrl(video.platform_id)}
              playerRef={playerRef}
              onTimeUpdate={handleTimeUpdate}
              onDurationChange={handleDurationChange}
            />
          </div>
          {/* Detail Panel — right sidebar */}
          <div className="w-[380px] shrink-0 overflow-y-auto custom-scrollbar">
            <VideoDetailPanel
              video={video}
              onClose={handleBack}
              onUpdate={handleUpdate}
              onDelete={handleDelete}
              collections={collections}
              videoCollectionIds={selectedVideoCollectionIds}
              onToggleCollection={handleToggleVideoCollection}
              onCreateCollection={handleCreateCollection}
              hidePreview
            />
          </div>
        </div>
      </div>
    </div>
  );
}
