import React, { useState } from 'react';
import {
  ChevronRight,
  ChevronLeft,
  X,
  MonitorPlay,
  FileText,
  BookOpen,
  ScanEye,
  Sparkles,
  Star,
  Plus,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Video, Tag } from '../../types';
import { EagleTagPicker } from '../EagleTagPicker';
import { ResourcePromptSection } from '../resources/ResourcePromptSection';
import { AIStatusBadge } from './AIStatusBadge';
import type { ResourceData } from './useDownloadsData';
import { getCoverUrl, formatResolution } from '../../utils/awemeType';

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
  /** Notified when ResourcePromptSection's ensure-trigger-tag flow assigns a
   *  new tag, or its self-managed Generate flow completes (the caption
   *  workflow may attach AI tags too) — so the Tags block above (fed by
   *  selectedVideoTags) refreshes. */
  onTagsChanged?: () => void;
  /** Island shell: render as a bare integrated column (no fixed overlay / resize
   *  handle / expand tab) to be portaled into the shell's info island — mirrors
   *  the uploads ResourceInfoPanel so the two sidebars match. */
  bare?: boolean;
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
  onTagsChanged,
  bare = false,
}) => {
  const { t } = useTranslation();

  // Local edit state powers the island dashed click-to-edit Notes affordance.
  const [editingNotes, setEditingNotes] = useState(false);

  const cPrimary = 'text-content';
  const cText300 = 'text-content-2';
  const cText400 = 'text-content-2';
  const cLabel = 'text-content-3';
  const cFaint700 = 'text-content-4';
  const cHoverPrimary = 'hover:text-content';
  const cSurface = 'bg-island';
  const cBorderWrap = 'border-line';
  const cBorderHeader = 'border-line';
  const cBorderSection = 'border-line';
  const cInputBg = 'bg-island-2';
  const cInputBorder = 'border-line';
  const cHoverSurface = 'hover:bg-island-2';
  const cChipBg = 'bg-island-2';
  const cChipText = 'text-content-3';
  const cChipBorder = 'border-line';

  // AI state lives on `resources` (migration 067/075 moved it off
  // parsed_media), so `selectedResourceData` is the real source and the
  // selectedVideo fields are only a fallback for callers that hydrate the
  // media row themselves (search hits, the backend card projection).
  const transcriptStatus =
    selectedResourceData?.transcript_status ?? selectedVideo.transcript_status;
  const summaryStatus =
    selectedResourceData?.summary_status ?? selectedVideo.summary_status;
  const analysisStatus =
    selectedResourceData?.visual_analysis_status ?? selectedVideo.visual_analysis_status;
  const hasPrompt = selectedResourceData?.has_prompt ?? selectedVideo.has_prompt ?? false;

  const body = (
    <div className="flex-1 overflow-y-auto">
          {/* Header */}
          <div className={`sticky top-0 z-10 flex items-center justify-between px-4 py-2 border-b ${cBorderHeader} ${cSurface}`}>
            <h3 className={`text-sm font-semibold ${cPrimary} truncate`}>{t('resources.infoPanel.title')}</h3>
            <button
              onClick={onClose}
              className={`p-1.5 ${cText400} ${cHoverPrimary} ${cHoverSurface} rounded-lg transition-colors`}
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
          {selectedResourceData && (
            <div className="px-4 mt-3">
              <h4 className="text-[11px] font-semibold text-content-3 uppercase tracking-widest mb-1.5">
                {t('resources.infoPanel.notes')}
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
                  placeholder={t('resources.infoPanel.notesPlaceholder')}
                  rows={3}
                  className="w-full bg-island-2 border border-line rounded-lg px-2.5 py-2 text-xs text-content-2 placeholder-content-4 focus:outline-none focus:border-indigo-500/50 resize-none"
                />
              ) : (
                <button
                  onClick={() => setEditingNotes(true)}
                  className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-content-4 hover:text-content-2 border border-dashed border-line-strong hover:border-line-strong rounded-lg transition-colors text-left"
                >
                  <Plus size={12} className="shrink-0" />
                  {t('resources.infoPanel.addNote')}
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

          {selectedResourceData?.id && (
            <ResourcePromptSection resourceId={selectedResourceData.id} onTagsChanged={onTagsChanged} />
          )}

          {/* Platform hashtags (read-only) */}
          {selectedVideo?.hashtags && (
            <div className="px-4 mt-3">
              <h4 className={`text-[11px] font-semibold ${cLabel} uppercase tracking-widest mb-2`}>
                {t('resources.infoPanel.platformTags')}
              </h4>
              <div className="flex flex-wrap gap-1.5">
                {selectedVideo.hashtags.split(/\s+/).filter(h => h.startsWith('#') && h.length > 1).map((ht, i) => (
                  <span key={i} className={`text-[10px] px-2 py-0.5 rounded ${cChipBg} ${cChipText} border ${cChipBorder}`}>
                    {ht}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* AI Status */}
          {(transcriptStatus || summaryStatus || analysisStatus || hasPrompt) && (
            <div className={`px-4 mt-4 border-t ${cBorderSection} pt-3`}>
              <h4 className={`text-[11px] font-semibold ${cLabel} uppercase tracking-widest mb-2`}>
                {t('resources.infoPanel.aiStatus')}
              </h4>
              <div className="space-y-1.5">
                {transcriptStatus && transcriptStatus !== 'none' && (
                  <div className="flex items-center justify-between py-1">
                    <div className="flex items-center gap-2">
                      <FileText size={12} className="text-[var(--accent-text)]" />
                      <span className={`text-xs ${cText400}`}>Transcript</span>
                    </div>
                    <AIStatusBadge status={transcriptStatus} />
                  </div>
                )}
                {summaryStatus && summaryStatus !== 'none' && (
                  <div className="flex items-center justify-between py-1">
                    <div className="flex items-center gap-2">
                      <BookOpen size={12} className="text-[var(--accent-text)]" />
                      <span className={`text-xs ${cText400}`}>Summary</span>
                    </div>
                    <AIStatusBadge status={summaryStatus} />
                  </div>
                )}
                {analysisStatus && analysisStatus !== 'none' && (
                  <div className="flex items-center justify-between py-1">
                    <div className="flex items-center gap-2">
                      <ScanEye size={12} className="text-purple-400" />
                      <span className={`text-xs ${cText400}`}>Visual Analysis</span>
                    </div>
                    <AIStatusBadge status={analysisStatus} />
                  </div>
                )}
                {/* Prompt is a has-it/doesn't, not a pipeline state — no
                    AIStatusBadge (which renders processing/failed states this
                    row can never be in). The panel's own PromptSection above
                    is where the text lives; this line just mirrors the card. */}
                {hasPrompt && (
                  <div className="flex items-center justify-between py-1">
                    <div className="flex items-center gap-2">
                      <Sparkles size={12} className="text-[var(--accent-text)]" />
                      <span className={`text-xs ${cText400}`}>Prompt</span>
                    </div>
                    <span className="text-[10px] text-[var(--accent-text)]">Yes</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Properties */}
          <div className={`px-4 mt-4 border-t ${cBorderSection} pt-3`}>
            <h4 className={`text-[11px] font-semibold ${cLabel} uppercase tracking-widest mb-2`}>
              {t('resources.infoPanel.properties')}
            </h4>
            <div className="space-y-0">
              {/* Rating — island merges it into the properties list (first row) to
                  mirror the uploads panel. Classic shows the standalone section above. */}
                <div className="flex justify-between items-center py-1.5 pr-0.5">
                  <span className="text-xs text-content-3">{t('resources.infoPanel.rating')}</span>
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
              {selectedVideo.author && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>{t('resources.infoPanel.author')}</span>
                  <span className={`text-xs ${cText300}`}>@{selectedVideo.author}</span>
                </div>
              )}
              {selectedVideo.duration && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>{t('resources.infoPanel.duration')}</span>
                  <span className={`text-xs ${cText300}`}>{selectedVideo.duration}s</span>
                </div>
              )}
              {selectedVideo.resolution && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>{t('resources.infoPanel.resolution')}</span>
                  <span className={`text-xs ${cText300}`}>{formatResolution(selectedVideo.resolution)}</span>
                </div>
              )}
              {selectedVideo.datasize && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>{t('resources.infoPanel.size')}</span>
                  <span className={`text-xs ${cText300}`}>{selectedVideo.datasize}</span>
                </div>
              )}
              {selectedVideo.source_platform && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>{t('resources.infoPanel.platform')}</span>
                  <span className={`text-xs ${cText300} capitalize`}>{selectedVideo.source_platform}</span>
                </div>
              )}
              {selectedVideo.published_at && (
                <div className="flex justify-between items-center py-1.5">
                  <span className={`text-xs ${cLabel}`}>{t('resources.infoPanel.published')}</span>
                  <span className={`text-xs ${cText300}`}>{formatDate(selectedVideo.published_at)}</span>
                </div>
              )}
            </div>
          </div>

          {/* Bottom padding */}
          <div className="pb-6" />
        </div>
  );

  // Island shell: bare integrated column — portaled into the info island by
  // DownloadsView so the My Downloads sidebar sits INSIDE the island frame (not
  // a floating overlay), matching the My Uploads ResourceInfoPanel.
  if (bare) {
    return <div className="h-full flex flex-col overflow-hidden">{body}</div>;
  }

  return (
    <>
      {/* Info Panel — classic floating overlay */}
      <div
        className={`hidden md:flex fixed top-14 bottom-0 right-0 z-40 ${cSurface} border-l ${cBorderWrap} transition-transform duration-300 ease-in-out shadow-2xl ${
          showInfoPanel ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ width: `${infoPanelWidth}px` }}
      >
        <button
          onClick={() => onTogglePanel(false)}
          className={`absolute -left-10 bottom-8 w-10 h-12 ${cSurface} border-l border-y ${cBorderWrap} rounded-l-xl flex items-center justify-center ${cText400} ${cHoverPrimary} cursor-pointer ${cHoverSurface} transition-colors z-10`}
        >
          <ChevronRight size={20} />
        </button>
        <div
          onMouseDown={onResizeStart}
          className="w-1 h-full cursor-col-resize shrink-0 hover:bg-blue-500 active:bg-blue-500 transition-colors"
        />
        {body}
      </div>

      {/* Expand tab — visible when panel is closed */}
      {!showInfoPanel && (
        <button
          onClick={() => onTogglePanel(true)}
          className={`hidden md:flex fixed bottom-8 right-0 w-10 h-12 ${cSurface} border-l border-y ${cBorderWrap} rounded-l-xl items-center justify-center ${cText400} ${cHoverPrimary} cursor-pointer ${cHoverSurface} transition-all z-50`}
        >
          <ChevronLeft size={20} />
        </button>
      )}
    </>
  );
};
