import React, { useState, useEffect } from 'react';
import { X, Search, Video, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { fetchLibrary } from '../services/dataService';
import { linkVideoToProject } from '../services/projectsService';
import { Video as VideoType } from '../types';
import { getCoverUrl } from '../utils/awemeType';
import { useAuth } from '../contexts/AuthContext';

interface LinkVideoModalProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: string;
  onLinked: () => void;
}

export const LinkVideoModal: React.FC<LinkVideoModalProps> = ({
  isOpen,
  onClose,
  projectId,
  onLinked,
}) => {
  const { t } = useTranslation();
  const { mediaToken } = useAuth();
  const [videos, setVideos] = useState<VideoType[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isLinking, setIsLinking] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      loadVideos();
    }
  }, [isOpen]);

  const loadVideos = async () => {
    setIsLoading(true);
    try {
      const data = await fetchLibrary();
      setVideos(data);
    } catch (err) {
      console.error('Failed to load videos:', err);
    } finally {
      setIsLoading(false);
    }
  };

  if (!isOpen) return null;

  const filteredVideos = videos.filter(v => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      (v.title && v.title.toLowerCase().includes(q)) ||
      (v.author && v.author.toLowerCase().includes(q)) ||
      (v.description && v.description.toLowerCase().includes(q))
    );
  });

  const handleLink = async (video: VideoType) => {
    if (!video.id) return;
    setIsLinking(video.id);
    try {
      await linkVideoToProject(projectId, video.id);
      onLinked();
    } catch (err) {
      console.error('Failed to link video:', err);
    } finally {
      setIsLinking(null);
    }
  };

  const handleClose = () => {
    setSearchQuery('');
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={handleClose}
      />

      {/* Modal */}
      <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[80vh] flex flex-col animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-zinc-800 flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-500/20 rounded-lg">
              <Video size={20} className="text-indigo-400" />
            </div>
            <h2 className="text-lg font-semibold text-white">
              {t('mediatrack.selectVideo')}
            </h2>
          </div>
          <button
            onClick={handleClose}
            className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Search */}
        <div className="px-6 pt-4 flex-shrink-0">
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('mediatrack.searchVideos')}
              className="w-full pl-10 pr-4 py-2.5 bg-zinc-800 border border-zinc-700 rounded-xl text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all text-sm"
            />
          </div>
        </div>

        {/* Video list */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-2">
          {isLoading && (
            <div className="flex items-center justify-center py-10">
              <Loader2 className="w-6 h-6 text-indigo-400 animate-spin" />
            </div>
          )}

          {!isLoading && filteredVideos.length === 0 && (
            <div className="text-center py-10 text-zinc-500 text-sm">
              No videos found
            </div>
          )}

          {!isLoading && filteredVideos.map(video => (
            <button
              key={video.id || video.platform_id}
              onClick={() => handleLink(video)}
              disabled={isLinking !== null}
              className="w-full flex items-center gap-3 p-3 bg-zinc-800/60 hover:bg-zinc-800 border border-zinc-700/30 hover:border-zinc-600 rounded-xl transition-all text-left disabled:opacity-50"
            >
              {/* Thumbnail — local-only; show <Video> icon if not yet downloaded */}
              <div className="w-16 h-12 bg-zinc-700 rounded-lg flex-shrink-0 overflow-hidden flex items-center justify-center">
                {(() => {
                  const coverUrl = getCoverUrl(video, mediaToken ?? undefined);
                  return coverUrl ? (
                    <img
                      src={coverUrl}
                      alt=""
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  ) : (
                    <Video size={20} className="text-zinc-500" />
                  );
                })()}
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <p className="text-sm text-white truncate font-medium">
                  {video.title || video.platform_id}
                </p>
                {video.author && (
                  <p className="text-xs text-zinc-500 mt-0.5">{video.author}</p>
                )}
              </div>

              {/* Loading state */}
              {isLinking === video.id && (
                <Loader2 size={16} className="text-indigo-400 animate-spin flex-shrink-0" />
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
