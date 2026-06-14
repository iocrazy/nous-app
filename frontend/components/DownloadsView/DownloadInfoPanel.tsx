import React, { useState } from 'react';
import {
  ChevronRight,
  ChevronLeft,
  X,
  MonitorPlay,
  Brain,
  Sparkles,
  Eye,
  Star,
  Plus,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Video, Tag } from '../../types';
import { EagleTagPicker } from '../EagleTagPicker';
import { AIStatusBadge } from './AIStatusBadge';
import { getCoverUrl, formatResolution } from '../../utils/awemeType';
import { islandUI } from '../../utils/featureFlags';

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

  // Island shell (spec D1–D12): align this panel to the uploads panel
  // (ResourceInfoPanel) in color + layout. The flag is global (VITE_FEATURE_ISLAND_UI);
  // when OFF (classic) every const resolves to the exact original ink class and the
  // layout branches fall to the original markup → D12 byte-identical.
  const island = islandUI();
  // Local edit state powers the island dashed click-to-edit Notes affordance.
  const [editingNotes, setEditingNotes] = useState(false);

  const cPrimary = island ? 'text-content' : 'text-ink-50';
  const cText300 = island ? 'text-content-2' : 'text-ink-300';
  const cText400 = island ? 'text-content-2' : 'text-ink-400';
  const cLabel = island ? 'text-content-3' : 'text-ink-500';
  const cFaint700 = island ? 'text-content-4' : 'text-ink-700';
  const cHoverPrimary = island ? 'hover:text-content' : 'hover:text-ink-50';
  const cSurface = island ? 'bg-island' : 'bg-ink-900';
  const cBorderWrap = island ? 'border-line' : 'border-ink-800';
  const cBorderHeader = island ? 'border-line' : 'border-ink-800/80';
  const cBorderSection = island ? 'border-line' : 'border-ink-800/60';

  return (
    <>
      {/* Info Panel */}
      <div
        className={`hidden md:flex fixed top-14 bottom-0 right-0 z-40 ${cSurface} border-l ${cBorderWrap} transition-transform duration-300 ease-in-out shadow-2xl ${
          showInfoPanel ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ width: `${infoPanelWidth}px` }}
      >
        <button
          onClick={() => onTogglePanel(false)}
          className={`absolute -left-10 bottom-8 w-10 h-12 ${cSurface} border-l border-y ${cBorderWrap} rounded-l-xl flex items-center justify-center ${cText400} ${cHoverPrimary} cursor-pointer hover:bg-ink-800 transition-colors z-10`}
        >
          <ChevronRight size={20} />
        </button>

        <div
          onMouseDown={onResizeStart}
          className="w-1 h-full cursor-col-resize shrink-0 hover:bg-blue-500 active:bg-blue-500 transition-colors"
        />

        <div className="flex-1 overflow-y-auto">
          {/* Header */}
          <div className={`sticky top-0 z-10 flex items-center justify-between px-4 py-2 border-b ${cBorderHeader} ${cSurface}`}>
            <h3 className={`text-sm font-semibold ${cPrimary} truncate`}>{t('resources.details', 'Details')}</h3>
            <button
              onClick={onClose}
              className={`p-1.5 ${cText400} ${cHoverPrimary} hover:bg-ink-800 rounded-lg transition-colors`}
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
                <MonitorPlay size={32} className={cFaint700} />
              </div>
            )}
          </div>

          {/* Title */}
          <div className="px-4 mt-4">
            <h4 className={`text-sm font-medium ${cPrimary} break-words leading-snug`}>
              {selectedVideo.title || 'Untitled'}
            </h4>
            {selectedVideo.description && (
              <p className={`mt-1.5 text-xs ${cLabel} line-clamp-3`}>
                {selectedVideo.description}
              </p>
            )}
          </div>

          {/* Notes — island layout: click-to-edit dashed row, placed right after the
              title to mirror the uploads panel (ResourceInfoPanel). Classic keeps its
              original textarea block further down (rendered only when !island). */}
          {island && selectedResourceData && (
            <div className="px-4 mt-3">
              <h4 className="text-[11px] font-semibold text-content-3 uppercase tracking-widest mb-1.5">
                Notes
              </h4>
              {panelNotes || editingNotes ? (
                <textarea
                  value={panelNotes}
                  onChange={e => onNotesChange(e.target.value)}
                  onBlur={() => {
                    onNotesBlur();
                    if (!panelNotes.trim()) setEditingNotes(false);
                  }}
                  autoFocus={editingNotes && !panelNotes}
                  placeholder="Add notes..."
                  rows={3}
                  className="w-full bg-ink-800/50 border border-ink-700/50 rounded-lg px-2.5 py-2 text-xs text-content-2 placeholder-content-4 focus:outline-none focus:border-indigo-500/50 resize-none"
                />
              ) : (
                <button
                  onClick={() => setEditingNotes(true)}
                  className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-content-4 hover:text-content-2 border border-dashed border-line-strong hover:border-line-strong rounded-lg transition-colors text-left"
                >
                  <Plus size={12} className="shrink-0" />
                  Add note
                </button>
              )}
            </div>
          )}

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
              <h4 className={`text-[11px] font-semibold ${cLabel} uppercase tracking-widest mb-2`}>
                Platform Tags
              </h4>
              <div className="flex flex-wrap gap-1.5">
                {selectedVideo.hashtags.split(/\s+/).filter(h => h.startsWith('#') && h.length > 1).map((ht, i) => (
                  <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-ink-800/50 text-ink-500 border border-ink-700/50">
                    {ht}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Rating — classic only: island merges rating into the PROPERTIES first
              row (matches the uploads panel). Classic keeps this standalone section. */}
          {!island && (
            <div className="px-4 mt-4">
              <h4 className="text-[11px] font-semibold text-ink-500 uppercase tracking-widest mb-2">
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
                        : 'text-ink-600'}
                    />
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Notes — classic only: island renders the dashed click-to-edit Notes row
              up near the title instead (see above). */}
          {!island && selectedResourceData && (
            <div className="px-4 mt-4">
              <h4 className="text-[11px] font-semibold text-ink-500 uppercase tracking-widest mb-1.5">
                Notes
              </h4>
              <textarea
                value={panelNotes}
                onChange={e => onNotesChange(e.target.value)}
                onBlur={onNotesBlur}
                placeholder="Add notes..."
                className="w-full bg-ink-900 border border-ink-800 rounded-lg px-3 py-2 text-xs text-ink-300 placeholder-ink-600 resize-none min-h-[60px] focus:outline-none focus:border-ink-600 transition-colors"
                rows={3}
              />
            </div>
          )}

          {/* AI Status */}
          {(selectedVideo.transcript_status || selectedVideo.summary_status || selectedVideo.visual_analysis_status) && (
            <div className={`px-4 mt-4 border-t ${cBorderSection} pt-3`}>
              <h4 className={`text-[11px] font-semibold ${cLabel} uppercase tracking-widest mb-2`}>
                AI
              </h4>
              <div className="space-y-1.5">
                {selectedVideo.transcript_status && selectedVideo.transcript_status !== 'none' && (
                  <div className="flex items-center justify-between py-1">
                    <div className="flex items-center gap-2">
                      <Brain size={12} className="text-indigo-400" />
                      <span className={`text-xs ${cText400}`}>Transcript</span>
                    </div>
                    <AIStatusBadge status={selectedVideo.transcript_status} />
                  </div>
                )}
                {selectedVideo.summary_status && selectedVideo.summary_status !== 'none' && (
                  <div className="flex items-center justify-between py-1">
                    <div className="flex items-center gap-2">
                      <Sparkles size={12} className="text-indigo-400" />
                      <span className={`text-xs ${cText400}`}>Summary</span>
                    </div>
                    <AIStatusBadge status={selectedVideo.summary_status} />
                  </div>
                )}
                {selectedVideo.visual_analysis_status && selectedVideo.visual_analysis_status !== 'none' && (
                  <div className="flex items-center justify-between py-1">
                    <div className="flex items-center gap-2">
                      <Eye size={12} className="text-purple-400" />
                      <span className={`text-xs ${cText400}`}>Visual Analysis</span>
                    </div>
                    <AIStatusBadge status={selectedVideo.visual_analysis_status} />
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Properties */}
          <div className={`px-4 mt-4 border-t ${cBorderSection} pt-3`}>
            <h4 className={`text-[11px] font-semibold ${cLabel} uppercase tracking-widest mb-2`}>
              Properties
            </h4>
            <div className="space-y-0">
              {/* Rating — island merges it into the properties list (first row) to
                  mirror the uploads panel. Classic shows the standalone section above. */}
              {island && (
                <div className="flex justify-between items-center py-1.5 pr-0.5">
                  <span className="text-xs text-content-3">Rating</span>
                  <div className="flex items-center gap-0.5" onMouseLeave={() => onHoverRating(0)}>
                    {[1, 2, 3, 4, 5].map(star => (
                      <button
                        key={star}
                        onClick={() => onRating(star)}
                        onMouseEnter={() => onHoverRating(star)}
                        className="p-0 transition-colors"
                      >
                        <Star
                          size={14}
                          className={(panelHoverRating || panelRating) >= star
                            ? 'text-amber-400 fill-amber-400'
                            : 'text-content-4'}
                        />
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {selectedVideo.author && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>Author</span>
                  <span className={`text-xs ${cText300}`}>@{selectedVideo.author}</span>
                </div>
              )}
              {selectedVideo.duration && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>Duration</span>
                  <span className={`text-xs ${cText300}`}>{selectedVideo.duration}s</span>
                </div>
              )}
              {selectedVideo.resolution && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>Resolution</span>
                  <span className={`text-xs ${cText300}`}>{formatResolution(selectedVideo.resolution)}</span>
                </div>
              )}
              {selectedVideo.datasize && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>Size</span>
                  <span className={`text-xs ${cText300}`}>{selectedVideo.datasize}</span>
                </div>
              )}
              {selectedVideo.source_platform && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>Platform</span>
                  <span className={`text-xs ${cText300} capitalize`}>{selectedVideo.source_platform}</span>
                </div>
              )}
              {selectedVideo.published_at && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>Published</span>
                  <span className={`text-xs ${cText300}`}>{formatDate(selectedVideo.published_at)}</span>
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
          className={`hidden md:flex fixed bottom-8 right-0 w-10 h-12 ${cSurface} border-l border-y ${cBorderWrap} rounded-l-xl items-center justify-center ${cText400} ${cHoverPrimary} cursor-pointer hover:bg-ink-800 transition-all z-50`}
        >
          <ChevronLeft size={20} />
        </button>
      )}
    </>
  );
};
