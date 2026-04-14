import React from 'react';
import {
  ChevronRight,
  ChevronLeft,
  X,
  MonitorPlay,
  Brain,
  Sparkles,
  Eye,
  Star,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Video, Tag } from '../../types';
import { EagleTagPicker } from '../EagleTagPicker';
import { AIStatusBadge } from './AIStatusBadge';
import { getCoverUrl, formatResolution } from '../../utils/awemeType';

interface ResourceData {
  id: string;
  notes: string | null;
  rating: number;
}

interface DownloadInfoPanelProps {
  selectedVideo: Video;
  showInfoPanel: boolean;
  infoPanelWidth: number;
  panelNotes: string;
  panelRating: number;
  panelHoverRating: number;
  selectedResourceData: ResourceData | undefined;
  selectedVideoTags: Array<{ tag: { id: string; name: string; color?: string } }>;
  allTags: Tag[];
  mediaToken: string | null | undefined;
  onClose: () => void;
  onTogglePanel: (show: boolean) => void;
  onResizeStart: (e: React.MouseEvent) => void;
  onAddTag: (tagId: string) => void;
  onRemoveTag: (tagId: string) => void;
  onCreateTag: (name: string, color: string) => Promise<Tag | null>;
  onRating: (star: number) => void;
  onHoverRating: (star: number) => void;
  onNotesChange: (notes: string) => void;
  onNotesBlur: () => void;
}

const formatDate = (isoString?: string) => {
  if (!isoString) return '';
  try {
    return new Date(isoString).toLocaleDateString();
  } catch { return ''; }
};

export const DownloadInfoPanel: React.FC<DownloadInfoPanelProps> = ({
  selectedVideo,
  showInfoPanel,
  infoPanelWidth,
  panelNotes,
  panelRating,
  panelHoverRating,
  selectedResourceData,
  selectedVideoTags,
  allTags,
  mediaToken,
  onClose,
  onTogglePanel,
  onResizeStart,
  onAddTag,
  onRemoveTag,
  onCreateTag,
  onRating,
  onHoverRating,
  onNotesChange,
  onNotesBlur,
}) => {
  const { t } = useTranslation();

  return (
    <>
      {/* Info Panel */}
      <div
        className={`hidden md:flex fixed top-14 bottom-0 right-0 z-40 bg-zinc-900 border-l border-zinc-800 transition-transform duration-300 ease-in-out shadow-2xl ${
          showInfoPanel ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ width: `${infoPanelWidth}px` }}
      >
        <button
          onClick={() => onTogglePanel(false)}
          className="absolute -left-10 bottom-8 w-10 h-12 bg-zinc-900 border-l border-y border-zinc-800 rounded-l-xl flex items-center justify-center text-zinc-400 hover:text-white cursor-pointer hover:bg-zinc-800 transition-colors z-10"
        >
          <ChevronRight size={20} />
        </button>

        <div
          onMouseDown={onResizeStart}
          className="w-1 h-full cursor-col-resize shrink-0 hover:bg-blue-500 active:bg-blue-500 transition-colors"
        />

        <div className="flex-1 overflow-y-auto">
          {/* Header */}
          <div className="sticky top-0 z-10 flex items-center justify-between px-4 py-2 border-b border-zinc-800/80 bg-zinc-900">
            <h3 className="text-sm font-semibold text-white truncate">{t('resources.details', 'Details')}</h3>
            <button
              onClick={onClose}
              className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
            >
              <X size={16} />
            </button>
          </div>

          {/* Cover */}
          <div className="relative w-full aspect-video bg-black">
            {getCoverUrl(selectedVideo, mediaToken ?? undefined) ? (
              <img
                src={getCoverUrl(selectedVideo, mediaToken ?? undefined)!}
                alt={selectedVideo.title || ''}
                className="w-full h-full object-contain"
                referrerPolicy="no-referrer"
              />
            ) : (
              <div className="w-full h-full flex items-center justify-center">
                <MonitorPlay size={32} className="text-zinc-700" />
              </div>
            )}
          </div>

          {/* Title */}
          <div className="px-4 mt-4">
            <h4 className="text-sm font-medium text-white break-words leading-snug">
              {selectedVideo.title || 'Untitled'}
            </h4>
            {selectedVideo.description && (
              <p className="mt-1.5 text-xs text-zinc-500 line-clamp-3">
                {selectedVideo.description}
              </p>
            )}
          </div>

          {/* Tags */}
          {selectedResourceData && (
            <EagleTagPicker
              assignedTags={selectedVideoTags.map(item => item.tag).filter((t): t is Tag => !!t)}
              allTags={allTags}
              onAdd={onAddTag}
              onRemove={onRemoveTag}
              onCreate={onCreateTag}
            />
          )}

          {/* Platform hashtags (read-only) */}
          {selectedVideo?.hashtags && (
            <div className="px-4 mt-3">
              <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                Platform Tags
              </h4>
              <div className="flex flex-wrap gap-1.5">
                {selectedVideo.hashtags.split(/\s+/).filter(h => h.startsWith('#') && h.length > 1).map((ht, i) => (
                  <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-zinc-800/50 text-zinc-500 border border-zinc-700/50">
                    {ht}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Rating */}
          <div className="px-4 mt-4">
            <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
              Rating
            </h4>
            <div className="flex items-center gap-0.5">
              {[1, 2, 3, 4, 5].map(star => (
                <button
                  key={star}
                  onClick={() => onRating(star)}
                  onMouseEnter={() => onHoverRating(star)}
                  onMouseLeave={() => onHoverRating(0)}
                  className="p-0.5 transition-colors"
                >
                  <Star
                    size={16}
                    className={(panelHoverRating || panelRating) >= star
                      ? 'text-amber-400 fill-amber-400'
                      : 'text-zinc-600'}
                  />
                </button>
              ))}
            </div>
          </div>

          {/* Notes */}
          {selectedResourceData && (
            <div className="px-4 mt-4">
              <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-1.5">
                Notes
              </h4>
              <textarea
                value={panelNotes}
                onChange={e => onNotesChange(e.target.value)}
                onBlur={onNotesBlur}
                placeholder="Add notes..."
                className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-xs text-zinc-300 placeholder-zinc-600 resize-none min-h-[60px] focus:outline-none focus:border-zinc-600 transition-colors"
                rows={3}
              />
            </div>
          )}

          {/* AI Status */}
          {(selectedVideo.transcript_status || selectedVideo.summary_status || selectedVideo.visual_analysis_status) && (
            <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
              <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                AI
              </h4>
              <div className="space-y-1.5">
                {selectedVideo.transcript_status && selectedVideo.transcript_status !== 'none' && (
                  <div className="flex items-center justify-between py-1">
                    <div className="flex items-center gap-2">
                      <Brain size={12} className="text-indigo-400" />
                      <span className="text-xs text-zinc-400">Transcript</span>
                    </div>
                    <AIStatusBadge status={selectedVideo.transcript_status} />
                  </div>
                )}
                {selectedVideo.summary_status && selectedVideo.summary_status !== 'none' && (
                  <div className="flex items-center justify-between py-1">
                    <div className="flex items-center gap-2">
                      <Sparkles size={12} className="text-indigo-400" />
                      <span className="text-xs text-zinc-400">Summary</span>
                    </div>
                    <AIStatusBadge status={selectedVideo.summary_status} />
                  </div>
                )}
                {selectedVideo.visual_analysis_status && selectedVideo.visual_analysis_status !== 'none' && (
                  <div className="flex items-center justify-between py-1">
                    <div className="flex items-center gap-2">
                      <Eye size={12} className="text-purple-400" />
                      <span className="text-xs text-zinc-400">Visual Analysis</span>
                    </div>
                    <AIStatusBadge status={selectedVideo.visual_analysis_status} />
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Properties */}
          <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
            <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
              Properties
            </h4>
            <div className="space-y-0">
              {selectedVideo.author && (
                <div className="flex justify-between items-center py-1.5">
                  <span className="text-xs text-zinc-500">Author</span>
                  <span className="text-xs text-zinc-300">@{selectedVideo.author}</span>
                </div>
              )}
              {selectedVideo.duration && (
                <div className="flex justify-between items-center py-1.5">
                  <span className="text-xs text-zinc-500">Duration</span>
                  <span className="text-xs text-zinc-300">{selectedVideo.duration}s</span>
                </div>
              )}
              {selectedVideo.resolution && (
                <div className="flex justify-between items-center py-1.5">
                  <span className="text-xs text-zinc-500">Resolution</span>
                  <span className="text-xs text-zinc-300">{formatResolution(selectedVideo.resolution)}</span>
                </div>
              )}
              {selectedVideo.datasize && (
                <div className="flex justify-between items-center py-1.5">
                  <span className="text-xs text-zinc-500">Size</span>
                  <span className="text-xs text-zinc-300">{selectedVideo.datasize}</span>
                </div>
              )}
              {selectedVideo.source_platform && (
                <div className="flex justify-between items-center py-1.5">
                  <span className="text-xs text-zinc-500">Platform</span>
                  <span className="text-xs text-zinc-300 capitalize">{selectedVideo.source_platform}</span>
                </div>
              )}
              {selectedVideo.published_at && (
                <div className="flex justify-between items-center py-1.5">
                  <span className="text-xs text-zinc-500">Published</span>
                  <span className="text-xs text-zinc-300">{formatDate(selectedVideo.published_at)}</span>
                </div>
              )}
            </div>
          </div>

          {/* Bottom padding */}
          <div className="pb-6" />
        </div>
      </div>

      {/* Expand tab — visible when panel is closed */}
      {!showInfoPanel && (
        <button
          onClick={() => onTogglePanel(true)}
          className="hidden md:flex fixed bottom-8 right-0 w-10 h-12 bg-zinc-900 border-l border-y border-zinc-800 rounded-l-xl items-center justify-center text-zinc-400 hover:text-white cursor-pointer hover:bg-zinc-800 transition-all z-50"
        >
          <ChevronLeft size={20} />
        </button>
      )}
    </>
  );
};
