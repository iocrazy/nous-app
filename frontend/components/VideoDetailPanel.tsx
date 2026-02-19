
import React, { useState, useEffect, useCallback } from 'react';
import {
  FileText, Sparkles, Eye, Loader2, Copy, Download, Check,
  Clock, Tag, ChevronRight, Brain, AlertCircle,
  Heart, MessageCircle, Share2, Bookmark, User, MonitorPlay,
  HardDrive, Calendar, ExternalLink, Trash2,
  Music, Video as VideoIcon, Image as ImageIcon, PenTool, Wand2, RefreshCw, Star,
} from 'lucide-react';
import { Video, TranscriptData, SummaryData } from '../types';
import {
  triggerTranscription, getTranscript,
  triggerSummary, getSummary,
  triggerVisualAnalysis,
} from '../services/aiService';
import { MediaTagPicker } from './MediaTagPicker';
import { useToast } from './Toast';
import { getDownloadUrl } from '../services/dataService';
import { getSupabaseClient } from '../supabaseClient';
import { isVideoType, getVideoUrl } from '../utils/awemeType';

interface VideoDetailPanelProps {
  video: Video;
  onClose: () => void;
  onUpdate?: (id: string, updates: Partial<Video>) => void;
  onDelete?: (id: string, deleteFiles: boolean) => Promise<void>;
  /** Hide video preview in MediaCard (when external player is already shown) */
  hidePreview?: boolean;
}

type TabKey = 'info' | 'transcript' | 'analysis';

// Format seconds to MM:SS
const formatTimestamp = (seconds: number): string => {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
};

// Generate SRT content from transcript segments
const generateSRT = (segments: TranscriptData['segments']): string => {
  return segments.map((seg, i) => {
    const formatSrtTime = (sec: number) => {
      const h = Math.floor(sec / 3600);
      const m = Math.floor((sec % 3600) / 60);
      const s = Math.floor(sec % 60);
      const ms = Math.round((sec % 1) * 1000);
      return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')},${ms.toString().padStart(3, '0')}`;
    };
    return `${i + 1}\n${formatSrtTime(seg.start)} --> ${formatSrtTime(seg.end)}\n${seg.text}`;
  }).join('\n\n');
};

// Download text as file
const downloadFile = (content: string, filename: string, mimeType: string) => {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

const formatNumber = (num?: number) => {
  if (!num) return '0';
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
  return num.toString();
};

const formatDate = (isoString?: string) => {
  if (!isoString) return '';
  try {
    return new Date(isoString).toLocaleDateString();
  } catch { return ''; }
};

// ─── InfoRow ────────────────────────────────────────────

const InfoRow = ({ label, value }: { label: string; value?: string | null }) => {
  if (!value) return null;
  return (
    <div className="flex justify-between items-center py-1.5">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="text-xs text-zinc-300 text-right">{value}</span>
    </div>
  );
};

// ─── AI Status Badge ────────────────────────────────────

const AIStatusBadge: React.FC<{ status?: string }> = ({ status }) => {
  switch (status) {
    case 'processing':
      return (
        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400">
          <Loader2 size={9} className="animate-spin" /> Processing
        </span>
      );
    case 'completed':
      return (
        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400">
          <Check size={9} /> Done
        </span>
      );
    case 'failed':
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-400">
          Failed
        </span>
      );
    case 'pending':
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-500/10 text-zinc-500">
          Pending
        </span>
      );
    default:
      return null;
  }
};

const getAIStatusClass = (status?: string): string => {
  switch (status) {
    case 'processing': return 'animate-spin text-indigo-400';
    case 'completed': return 'text-emerald-400';
    case 'failed': return 'text-red-400';
    default: return 'text-zinc-600';
  }
};

export const VideoDetailPanel: React.FC<VideoDetailPanelProps> = ({
  video,
  onClose,
  onUpdate,
  onDelete,
  hidePreview = false,
}) => {
  const [activeTab, setActiveTab] = useState<TabKey>('info');
  const [transcript, setTranscript] = useState<TranscriptData | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [visualAnalysisLoading, setVisualAnalysisLoading] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [visualAnalysisError, setVisualAnalysisError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // AI Feature States
  const [extractText, setExtractText] = useState<string | null>(video.ai_extract_text || null);
  const [rewrittenText, setRewrittenText] = useState<string | null>(video.ai_rewrite_text || null);
  const [analysisText, setAnalysisText] = useState<string | null>(video.ai_analyze_text || null);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  // Notes
  const [notesValue, setNotesValue] = useState(video.notes || '');

  // Rating
  const [ratingValue, setRatingValue] = useState(video.rating || 0);
  const [hoverRating, setHoverRating] = useState(0);

  // Download
  const [isDownloading, setIsDownloading] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [retrySuccess, setRetrySuccess] = useState(false);
  const [showRefetchMenu, setShowRefetchMenu] = useState(false);

  const { addToast } = useToast();
  const isVideo = isVideoType(video.media_type);
  const videoUrl = getVideoUrl(video);

  // Load existing transcript/summary when tab changes
  useEffect(() => {
    if (activeTab === 'transcript' && video.transcript_status === 'completed' && !transcript) {
      loadTranscript();
    }
    if (activeTab === 'analysis' && video.summary_status === 'completed' && !summary) {
      loadSummary();
    }
  }, [activeTab, video.transcript_status, video.summary_status]);

  const loadTranscript = useCallback(async () => {
    try {
      setTranscriptLoading(true);
      setTranscriptError(null);
      const data = await getTranscript(video.platform_id);
      setTranscript(data);
    } catch (err) {
      setTranscriptError(err instanceof Error ? err.message : 'Failed to load transcript');
    } finally {
      setTranscriptLoading(false);
    }
  }, [video.platform_id]);

  const loadSummary = useCallback(async () => {
    try {
      setSummaryLoading(true);
      setSummaryError(null);
      const data = await getSummary(video.platform_id);
      setSummary(data);
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : 'Failed to load summary');
    } finally {
      setSummaryLoading(false);
    }
  }, [video.platform_id]);

  const handleTranscribe = async () => {
    try {
      setTranscriptLoading(true);
      setTranscriptError(null);
      await triggerTranscription(video.platform_id);
      if (onUpdate) {
        onUpdate(video.platform_id, { transcript_status: 'processing' });
      }
    } catch (err) {
      setTranscriptError(err instanceof Error ? err.message : 'Failed to start transcription');
    } finally {
      setTranscriptLoading(false);
    }
  };

  const handleSummarize = async () => {
    try {
      setSummaryLoading(true);
      setSummaryError(null);
      await triggerSummary(video.platform_id);
      if (onUpdate) {
        onUpdate(video.platform_id, { summary_status: 'processing' });
      }
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : 'Failed to start summarization');
    } finally {
      setSummaryLoading(false);
    }
  };

  const handleVisualAnalysis = async () => {
    try {
      setVisualAnalysisLoading(true);
      setVisualAnalysisError(null);
      await triggerVisualAnalysis(video.platform_id);
      if (onUpdate) {
        onUpdate(video.platform_id, { visual_analysis_status: 'processing' });
      }
    } catch (err) {
      setVisualAnalysisError(err instanceof Error ? err.message : 'Failed to start visual analysis');
    } finally {
      setVisualAnalysisLoading(false);
    }
  };

  const handleCopyTranscript = () => {
    if (!transcript) return;
    const text = transcript.segments
      .map((s) => `[${formatTimestamp(s.start)}] ${s.text}`)
      .join('\n');
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleExportSRT = () => {
    if (!transcript) return;
    const srt = generateSRT(transcript.segments);
    downloadFile(srt, `${video.platform_id}_transcript.srt`, 'text/srt');
  };

  const handleExportTXT = () => {
    if (!transcript) return;
    downloadFile(transcript.text, `${video.platform_id}_transcript.txt`, 'text/plain');
  };

  const getStatusIndicator = (status?: string) => {
    switch (status) {
      case 'processing':
        return <span className="w-2 h-2 rounded-full bg-indigo-400 animate-pulse" />;
      case 'completed':
        return <span className="w-2 h-2 rounded-full bg-emerald-400" />;
      case 'failed':
        return <span className="w-2 h-2 rounded-full bg-red-400" />;
      default:
        return null;
    }
  };

  // Rating handler
  const handleRating = (star: number) => {
    const newRating = star === ratingValue ? 0 : star;
    setRatingValue(newRating);
    onUpdate?.(video.platform_id, { rating: newRating });
  };

  // Notes auto-save on blur
  const handleNotesBlur = () => {
    if (notesValue !== (video.notes || '')) {
      onUpdate?.(video.platform_id, { notes: notesValue });
    }
  };

  // Save AI content to DB
  const saveAIContent = (field: string, content: string) => {
    if (onUpdate && video.platform_id) {
      onUpdate(video.platform_id, { [field]: content, ai_generated_at: new Date().toISOString() });
    }
  };

  // AI action handler
  const handleAction = async (action: string) => {
    const platformId = video.platform_id;

    if (action === 'copy') {
      const textToCopy = video.description || video.title || '';
      if (textToCopy) {
        navigator.clipboard.writeText(textToCopy).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        });
      }
    } else if (action === 'extract') {
      if (!platformId) return;
      setLoadingAction('extract');
      try {
        if (video.transcript_status === 'completed') {
          const result = await getTranscript(platformId);
          const text = result.text || '';
          setExtractText(text);
          saveAIContent('ai_extract_text', text);
          addToast('Transcription loaded', 'success');
        } else {
          await triggerTranscription(platformId);
          if (onUpdate) onUpdate(platformId, { transcript_status: 'processing' });
          addToast('Transcription started. Check back shortly.', 'info');
        }
      } catch (err) {
        console.error('Extract failed:', err);
        addToast('Extract failed', 'error');
      } finally {
        setLoadingAction(null);
      }
    } else if (action === 'rewrite') {
      if (!platformId) return;
      setLoadingAction('rewrite');
      try {
        await triggerSummary(platformId);
        if (onUpdate) onUpdate(platformId, { summary_status: 'processing' });
        addToast('Summary generation started.', 'info');
      } catch (err) {
        console.error('Rewrite failed:', err);
        addToast('Rewrite failed', 'error');
      } finally {
        setLoadingAction(null);
      }
    } else if (action === 'analyze') {
      if (!platformId) return;
      setLoadingAction('analyze');
      try {
        await triggerVisualAnalysis(platformId);
        if (onUpdate) onUpdate(platformId, { visual_analysis_status: 'processing' });
        addToast('Visual analysis started.', 'info');
      } catch (err) {
        console.error('Analyze failed:', err);
        addToast('Analysis failed', 'error');
      } finally {
        setLoadingAction(null);
      }
    }
  };

  // Download handler
  const onDownloadVideo = async () => {
    if (!video.platform_id) return;
    setIsDownloading(true);
    try {
      const { data: sessionData } = await getSupabaseClient()?.auth.getSession() || {};
      const token = sessionData?.session?.access_token;
      if (!token) {
        if (videoUrl) window.open(videoUrl, '_blank');
        return;
      }
      const downloadUrlStr = getDownloadUrl(video.platform_id);
      const response = await fetch(downloadUrlStr, { headers: { 'Authorization': `Bearer ${token}` } });
      if (!response.ok) throw new Error(`Download failed: ${response.status}`);
      const contentDisposition = response.headers.get('Content-Disposition');
      let filename = `${video.platform_id}.mp4`;
      if (contentDisposition) {
        const match = contentDisposition.match(/filename="(.+)"/);
        if (match) filename = match[1];
      }
      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(blobUrl);
    } catch (error) {
      console.error('Download error:', error);
      if (videoUrl) window.open(videoUrl, '_blank');
    } finally {
      setIsDownloading(false);
    }
  };

  const onDownloadAudio = () => {
    const url = video.music_download_urls?.[0];
    if (!url || url === '#') return;
    const audioLink = document.createElement('a');
    audioLink.href = url;
    audioLink.download = `${video.platform_id || 'music'}.mp3`;
    audioLink.target = '_blank';
    document.body.appendChild(audioLink);
    audioLink.click();
    document.body.removeChild(audioLink);
  };

  const onRefetch = async (options: { video?: boolean; music?: boolean; cover?: boolean }) => {
    if (!video.platform_id || !video.original_url) return;
    setIsRetrying(true);
    setRetrySuccess(false);
    setShowRefetchMenu(false);
    try {
      const { data: sessionData } = await getSupabaseClient()?.auth.getSession() || {};
      const token = sessionData?.session?.access_token;
      if (!token) return;
      const apiUrl = import.meta.env?.VITE_API_URL || 'http://localhost:8080';
      const response = await fetch(`${apiUrl}/api/v1/videos/fetch`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: video.original_url, video_bool: options.video ?? false, music_bool: options.music ?? false, cover_bool: options.cover ?? false }),
      });
      if (!response.ok) throw new Error(`Refetch failed: ${response.status}`);
      setRetrySuccess(true);
      setTimeout(() => setRetrySuccess(false), 3000);
    } catch (error) {
      console.error('Refetch error:', error);
    } finally {
      setIsRetrying(false);
    }
  };

  const tabs: { key: TabKey; label: string }[] = [
    { key: 'info', label: 'Info' },
    { key: 'transcript', label: 'Transcript' },
    { key: 'analysis', label: 'Analysis' },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Tab Navigation */}
      <div className="flex border-b border-zinc-800 shrink-0">
        {tabs.map((tab) => {
          const status = tab.key === 'transcript'
            ? video.transcript_status
            : tab.key === 'analysis'
            ? video.summary_status
            : undefined;

          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex-1 px-3 py-2 text-xs font-medium transition-colors flex items-center justify-center gap-1 ${
                activeTab === tab.key
                  ? 'text-zinc-200 border-b-2 border-indigo-500'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {tab.label}
              {getStatusIndicator(status)}
            </button>
          );
        })}
      </div>

      {/* Tab Content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {/* Info Tab — AI Creation Workbench */}
        {activeTab === 'info' && (
          <div className="animate-in fade-in duration-300">
            {/* 1. Title */}
            <div className="px-4 pt-4">
              <h4 className="text-sm font-medium text-white break-words leading-snug">
                {video.title || video.description || 'Untitled'}
              </h4>
            </div>

            {/* 2. Description preview */}
            {video.description && (
              <div className="px-4 mt-1.5">
                <p className="text-xs text-zinc-500 line-clamp-4">{video.description}</p>
              </div>
            )}

            {/* 3. Time + Duration */}
            <div className="px-4 mt-2 flex items-center gap-3 text-[11px] text-zinc-500">
              {video.published_at && (
                <span className="flex items-center gap-1">
                  <Calendar size={11} />
                  {formatDate(video.published_at)}
                </span>
              )}
              {video.duration && (
                <span className="flex items-center gap-1">
                  <Clock size={11} />
                  {video.duration}s
                </span>
              )}
            </div>

            {/* 4. Engagement 2x2 */}
            <div className="px-4 mt-3">
              <div className="grid grid-cols-2 gap-1.5">
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-rose-500/5 border border-rose-500/10">
                  <Heart size={12} className="text-rose-400 shrink-0" />
                  <span className="text-xs text-zinc-300">{formatNumber(video.like_count)}</span>
                </div>
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-sky-500/5 border border-sky-500/10">
                  <MessageCircle size={12} className="text-sky-400 shrink-0" />
                  <span className="text-xs text-zinc-300">{formatNumber(video.comment_count)}</span>
                </div>
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-emerald-500/5 border border-emerald-500/10">
                  <Share2 size={12} className="text-emerald-400 shrink-0" />
                  <span className="text-xs text-zinc-300">{formatNumber(video.share_count)}</span>
                </div>
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-amber-500/5 border border-amber-500/10">
                  <Bookmark size={12} className="text-amber-400 shrink-0" />
                  <span className="text-xs text-zinc-300">{formatNumber(video.favorite_count)}</span>
                </div>
              </div>
            </div>

            {/* 5. Tags (editable) */}
            <div className="px-4 mt-4">
              <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                Tags
              </h4>
              {video.id ? (
                <MediaTagPicker mediaId={video.id} initialTagNames={video.tags || []} />
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {(video.tags || []).map((tag, i) => (
                    <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">
                      #{tag}
                    </span>
                  ))}
                  {(!video.tags || video.tags.length === 0) && (
                    <span className="text-[10px] text-zinc-600 italic">No tags</span>
                  )}
                </div>
              )}
            </div>

            {/* 6. Rating + AI Status icons on same line */}
            <div className="px-4 mt-4 flex items-center justify-between">
              {/* Rating stars */}
              <div className="flex items-center gap-0.5">
                {[1, 2, 3, 4, 5].map((star) => (
                  <button
                    key={star}
                    onClick={() => handleRating(star)}
                    onMouseEnter={() => setHoverRating(star)}
                    onMouseLeave={() => setHoverRating(0)}
                    className="p-0.5 transition-colors"
                  >
                    <Star
                      size={14}
                      className={
                        star <= (hoverRating || ratingValue)
                          ? 'text-amber-400 fill-amber-400'
                          : 'text-zinc-600'
                      }
                    />
                  </button>
                ))}
              </div>
              {/* AI Status icons */}
              <div className="flex items-center gap-2">
                <FileText size={14} className={getAIStatusClass(video.transcript_status)} title={`Transcript: ${video.transcript_status || 'none'}`} />
                <Sparkles size={14} className={getAIStatusClass(video.summary_status)} title={`Summary: ${video.summary_status || 'none'}`} />
                <Eye size={14} className={getAIStatusClass(video.visual_analysis_status)} title={`Visual Analysis: ${video.visual_analysis_status || 'none'}`} />
              </div>
            </div>

            {/* 7. AI Action Buttons 4-column grid */}
            <div className="px-4 mt-3">
              <div className="grid grid-cols-4 gap-1.5">
                <button
                  onClick={() => handleAction('copy')}
                  className="flex items-center justify-center gap-1 p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors border border-zinc-700 hover:border-zinc-600"
                >
                  {copied ? <Check size={14} className="text-green-500 shrink-0" /> : <Copy size={14} className="shrink-0" />}
                  <span className="text-[10px] font-medium truncate">{copied ? 'Copied' : 'Copy'}</span>
                </button>
                <button
                  onClick={() => handleAction('extract')}
                  disabled={loadingAction === 'extract'}
                  className="ai-btn ai-btn-extract flex items-center justify-center gap-1 p-2 rounded-lg text-teal-300 hover:text-teal-100"
                >
                  {loadingAction === 'extract' ? <Loader2 size={14} className="animate-spin relative z-10 shrink-0" /> : <FileText size={14} className="relative z-10 shrink-0" />}
                  <span className="ai-text text-[10px] relative z-10 truncate">Extract</span>
                </button>
                <button
                  onClick={() => handleAction('rewrite')}
                  disabled={loadingAction === 'rewrite'}
                  className="ai-btn ai-btn-rewrite flex items-center justify-center gap-1 p-2 rounded-lg text-violet-300 hover:text-violet-100"
                >
                  {loadingAction === 'rewrite' ? <Loader2 size={14} className="animate-spin relative z-10 shrink-0" /> : <PenTool size={14} className="relative z-10 shrink-0" />}
                  <span className="ai-text text-[10px] relative z-10 truncate">Rewrite</span>
                </button>
                <button
                  onClick={() => handleAction('analyze')}
                  disabled={loadingAction === 'analyze'}
                  className="ai-btn ai-btn-analyze flex items-center justify-center gap-1 p-2 rounded-lg text-indigo-300 hover:text-indigo-100"
                >
                  {loadingAction === 'analyze' ? <Loader2 size={14} className="animate-spin relative z-10 shrink-0" /> : <Wand2 size={14} className="relative z-10 shrink-0" />}
                  <span className="ai-text text-[10px] relative z-10 truncate">Analyze</span>
                </button>
              </div>
            </div>

            {/* 8. Notes */}
            <div className="px-4 mt-4">
              <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                Notes
              </h4>
              <textarea
                value={notesValue}
                onChange={(e) => setNotesValue(e.target.value)}
                onBlur={handleNotesBlur}
                placeholder="Add notes..."
                rows={3}
                className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-xs text-zinc-300 placeholder-zinc-600 resize-none focus:outline-none focus:border-zinc-600 transition-colors"
              />
            </div>

            {/* 9. Description full */}
            {video.description && (
              <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
                <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                  Description
                </h4>
                <p className="text-xs text-zinc-400 whitespace-pre-wrap leading-relaxed">
                  {video.description}
                </p>
              </div>
            )}

            {/* 10. AI Results area */}
            {(extractText || rewrittenText || analysisText) && (
              <div className="px-4 mt-4 space-y-3">
                {extractText && (
                  <div className="animate-in fade-in slide-in-from-top-2">
                    <div className="flex items-center gap-2 mb-1.5">
                      <div className="p-1 rounded bg-teal-500/10 text-teal-400">
                        <FileText size={10} />
                      </div>
                      <span className="text-[10px] font-semibold text-teal-200">Extracted Data</span>
                    </div>
                    <div className="p-2.5 rounded-lg border border-teal-500/20 bg-teal-500/5 text-xs text-zinc-300 leading-relaxed whitespace-pre-wrap">
                      {extractText}
                    </div>
                  </div>
                )}
                {rewrittenText && (
                  <div className="animate-in fade-in slide-in-from-top-2">
                    <div className="flex items-center gap-2 mb-1.5">
                      <div className="p-1 rounded bg-indigo-500/10 text-indigo-400">
                        <PenTool size={10} />
                      </div>
                      <span className="text-[10px] font-semibold text-indigo-200">AI Rewrite</span>
                    </div>
                    <div className="p-2.5 rounded-lg border border-indigo-500/20 bg-indigo-500/5 text-xs text-zinc-300 leading-relaxed">
                      {rewrittenText}
                    </div>
                  </div>
                )}
                {analysisText && (
                  <div className="animate-in fade-in slide-in-from-top-2">
                    <div className="flex items-center gap-2 mb-1.5">
                      <div className="p-1 rounded bg-purple-500/10 text-purple-400">
                        <Wand2 size={10} />
                      </div>
                      <span className="text-[10px] font-semibold text-purple-200">Content Analysis</span>
                    </div>
                    <div className="p-2.5 rounded-lg border border-purple-500/20 bg-purple-500/5 text-xs text-zinc-300 leading-relaxed">
                      {analysisText}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* 11. Download footer */}
            <div className="px-4 mt-4 pt-3 border-t border-zinc-800/60 pb-3">
              <div className="flex gap-2">
                {isVideo && videoUrl && (
                  <button
                    onClick={onDownloadVideo}
                    disabled={isDownloading}
                    className="flex-1 flex items-center justify-center gap-2 bg-white hover:bg-zinc-100 text-black py-2 rounded-lg font-medium transition-colors text-xs disabled:opacity-70"
                  >
                    {isDownloading ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                    {isDownloading ? 'Downloading...' : 'Download'}
                  </button>
                )}

                {/* Refetch dropdown button */}
                <div className="relative">
                  <button
                    onClick={() => setShowRefetchMenu(!showRefetchMenu)}
                    disabled={isRetrying}
                    className={`w-9 h-9 flex items-center justify-center rounded-lg transition-colors border ${
                      retrySuccess
                        ? 'bg-emerald-600/20 text-emerald-400 border-emerald-600/50'
                        : 'bg-zinc-800 text-zinc-300 border-zinc-700 hover:bg-zinc-700 hover:text-white'
                    } disabled:opacity-70`}
                    title={retrySuccess ? 'Request submitted!' : 'Refetch Media'}
                  >
                    {isRetrying ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : retrySuccess ? (
                      <Check size={14} />
                    ) : (
                      <RefreshCw size={14} />
                    )}
                  </button>

                  {showRefetchMenu && (
                    <>
                      <div
                        className="fixed inset-0 z-40"
                        onClick={() => setShowRefetchMenu(false)}
                      />
                      <div className="absolute bottom-full right-0 mb-2 w-44 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl z-50 overflow-hidden">
                        <div className="py-1">
                          <button
                            onClick={() => onRefetch({ video: true })}
                            className="w-full px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
                          >
                            <VideoIcon size={14} className="text-indigo-400" />
                            Download Video
                          </button>
                          <button
                            onClick={() => onRefetch({ cover: true })}
                            className="w-full px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
                          >
                            <ImageIcon size={14} className="text-emerald-400" />
                            Download Cover
                          </button>
                          <button
                            onClick={() => onRefetch({ music: true })}
                            className="w-full px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
                          >
                            <Music size={14} className="text-amber-400" />
                            Download Audio
                          </button>
                          <div className="border-t border-zinc-700 my-1" />
                          <button
                            onClick={() => onRefetch({ video: true, cover: true, music: true })}
                            className="w-full px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
                          >
                            <Download size={14} className="text-sky-400" />
                            Download All
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* 12. Music row */}
            {video.need_download_music && video.music_download_urls && video.music_download_urls.length > 0 && (
              <div className="px-4 pb-4">
                <button
                  onClick={onDownloadAudio}
                  className="w-full flex items-center justify-center gap-2 px-3 py-2 text-xs font-medium text-amber-300 hover:text-amber-200 hover:bg-amber-900/20 border border-zinc-800 rounded-lg transition-colors"
                >
                  <Music size={14} />
                  Download Music
                  {video.music_name && <span className="text-zinc-500 truncate max-w-[120px]">({video.music_name})</span>}
                </button>
              </div>
            )}

            {/* Delete action */}
            {onDelete && (
              <div className="px-4 border-t border-zinc-800/60 pt-3 pb-6">
                <button
                  onClick={() => onDelete(video.platform_id, false)}
                  className="w-full flex items-center justify-center gap-2 px-3 py-2 text-xs font-medium text-red-400 hover:text-red-300 hover:bg-red-900/20 border border-zinc-800 rounded-lg transition-colors"
                >
                  <Trash2 size={14} />
                  Delete
                </button>
              </div>
            )}
          </div>
        )}

        {/* Transcript Tab */}
        {activeTab === 'transcript' && (
          <div className="p-4 space-y-4 animate-in fade-in duration-300">
            {/* Processing state */}
            {video.transcript_status === 'processing' && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <Loader2 size={32} className="animate-spin text-indigo-400 mb-4" />
                <h3 className="text-sm font-medium text-zinc-200">Transcribing...</h3>
                <p className="text-xs text-zinc-500 mt-1">This may take a few minutes.</p>
              </div>
            )}

            {/* Not started / pending */}
            {(!video.transcript_status || video.transcript_status === 'pending' || video.transcript_status === 'none') && !transcript && !transcriptLoading && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className="p-4 bg-zinc-800/50 rounded-full mb-4">
                  <FileText size={28} className="text-zinc-500" />
                </div>
                <h3 className="text-sm font-medium text-zinc-200">No Transcript Available</h3>
                <p className="text-xs text-zinc-500 mt-1 mb-4 max-w-[220px]">
                  Generate a transcript to see timestamped text from this media.
                </p>
                <button
                  onClick={handleTranscribe}
                  disabled={transcriptLoading}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                >
                  {transcriptLoading ? <Loader2 size={14} className="animate-spin" /> : <Brain size={14} />}
                  Transcribe
                </button>
                {transcriptError && (
                  <div className="mt-3 flex items-center gap-1.5 text-xs text-red-400">
                    <AlertCircle size={12} />
                    {transcriptError}
                  </div>
                )}
              </div>
            )}

            {/* Failed state */}
            {video.transcript_status === 'failed' && !transcript && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className="p-4 bg-red-500/10 rounded-full mb-4">
                  <AlertCircle size={28} className="text-red-400" />
                </div>
                <h3 className="text-sm font-medium text-zinc-200">Transcription Failed</h3>
                <p className="text-xs text-zinc-500 mt-1 mb-4">Something went wrong. Please try again.</p>
                <button
                  onClick={handleTranscribe}
                  disabled={transcriptLoading}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                >
                  {transcriptLoading ? <Loader2 size={14} className="animate-spin" /> : <Brain size={14} />}
                  Retry
                </button>
              </div>
            )}

            {/* Loading existing transcript */}
            {transcriptLoading && video.transcript_status === 'completed' && (
              <div className="flex items-center justify-center py-16">
                <Loader2 size={20} className="animate-spin text-indigo-400" />
              </div>
            )}

            {/* Transcript content */}
            {transcript && (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-[10px] text-zinc-500">
                  <span className="flex items-center gap-1">
                    <Clock size={10} />
                    {formatTimestamp(transcript.duration)} total
                  </span>
                  <span className="flex items-center gap-1">
                    <Tag size={10} />
                    {transcript.language.toUpperCase()}
                  </span>
                  <span>{transcript.segments.length} segments</span>
                </div>

                <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
                  <div className="max-h-[50vh] overflow-y-auto custom-scrollbar divide-y divide-zinc-800/50">
                    {transcript.segments.map((seg, i) => (
                      <div
                        key={i}
                        className="flex gap-2 px-3 py-2 hover:bg-zinc-800/30 transition-colors group"
                      >
                        <span className="text-[10px] font-mono text-indigo-400/70 group-hover:text-indigo-400 shrink-0 pt-0.5 transition-colors">
                          [{formatTimestamp(seg.start)}]
                        </span>
                        <p className="text-xs text-zinc-300 leading-relaxed">{seg.text}</p>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="flex flex-wrap gap-1.5">
                  <button
                    onClick={handleCopyTranscript}
                    className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors flex items-center gap-1.5 border border-zinc-700"
                  >
                    {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
                    {copied ? 'Copied!' : 'Copy'}
                  </button>
                  <button
                    onClick={handleExportSRT}
                    className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors flex items-center gap-1.5 border border-zinc-700"
                  >
                    <Download size={12} />
                    SRT
                  </button>
                  <button
                    onClick={handleExportTXT}
                    className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors flex items-center gap-1.5 border border-zinc-700"
                  >
                    <Download size={12} />
                    TXT
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Analysis Tab */}
        {activeTab === 'analysis' && (
          <div className="p-4 space-y-5 animate-in fade-in duration-300">
            {/* Summary Section */}
            <section className="space-y-2">
              <div className="flex items-center gap-2">
                <div className="p-1 bg-indigo-500/10 rounded text-indigo-400">
                  <Sparkles size={14} />
                </div>
                <h3 className="text-xs font-medium text-zinc-200">Summary</h3>
                {getStatusIndicator(video.summary_status)}
              </div>

              {video.summary_status === 'processing' && (
                <div className="flex items-center gap-2 p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                  <Loader2 size={14} className="animate-spin text-indigo-400" />
                  <span className="text-xs text-zinc-400">Generating summary...</span>
                </div>
              )}

              {(!video.summary_status || video.summary_status === 'pending' || video.summary_status === 'none') && !summary && !summaryLoading && (
                <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                  <p className="text-xs text-zinc-500 mb-2">Generate an AI summary with key points and topics.</p>
                  <button
                    onClick={handleSummarize}
                    disabled={summaryLoading}
                    className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {summaryLoading ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                    Summarize
                  </button>
                  {summaryError && (
                    <p className="mt-2 text-[10px] text-red-400 flex items-center gap-1">
                      <AlertCircle size={10} />
                      {summaryError}
                    </p>
                  )}
                </div>
              )}

              {video.summary_status === 'failed' && !summary && (
                <div className="p-3 bg-red-500/5 border border-red-500/20 rounded-lg">
                  <p className="text-xs text-red-400 mb-2">Summarization failed.</p>
                  <button
                    onClick={handleSummarize}
                    disabled={summaryLoading}
                    className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {summaryLoading ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                    Retry
                  </button>
                </div>
              )}

              {summaryLoading && video.summary_status === 'completed' && (
                <div className="flex items-center justify-center py-6">
                  <Loader2 size={16} className="animate-spin text-indigo-400" />
                </div>
              )}

              {summary && (
                <div className="space-y-3">
                  <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                    <p className="text-xs text-zinc-300 leading-relaxed">{summary.summary}</p>
                  </div>
                  {summary.key_points.length > 0 && (
                    <div>
                      <h4 className="text-[10px] font-medium text-zinc-400 uppercase tracking-wider mb-1.5">Key Points</h4>
                      <ul className="space-y-1">
                        {summary.key_points.map((point, i) => (
                          <li key={i} className="flex items-start gap-1.5 text-xs text-zinc-300">
                            <ChevronRight size={12} className="text-indigo-400 mt-0.5 shrink-0" />
                            {point}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {summary.topics.length > 0 && (
                    <div>
                      <h4 className="text-[10px] font-medium text-zinc-400 uppercase tracking-wider mb-1.5">Topics</h4>
                      <div className="flex flex-wrap gap-1.5">
                        {summary.topics.map((topic, i) => {
                          const colors = [
                            'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
                            'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
                            'bg-purple-500/10 text-purple-400 border-purple-500/20',
                            'bg-amber-500/10 text-amber-400 border-amber-500/20',
                            'bg-rose-500/10 text-rose-400 border-rose-500/20',
                            'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
                          ];
                          return (
                            <span key={i} className={`px-2 py-0.5 rounded-full text-[10px] font-medium border ${colors[i % colors.length]}`}>
                              {topic}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </section>

            {/* Visual Analysis Section */}
            <section className="space-y-2">
              <div className="flex items-center gap-2">
                <div className="p-1 bg-purple-500/10 rounded text-purple-400">
                  <Eye size={14} />
                </div>
                <h3 className="text-xs font-medium text-zinc-200">Visual Analysis</h3>
                {getStatusIndicator(video.visual_analysis_status)}
              </div>

              {video.visual_analysis_status === 'processing' && (
                <div className="flex items-center gap-2 p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                  <Loader2 size={14} className="animate-spin text-purple-400" />
                  <span className="text-xs text-zinc-400">Analyzing visual content...</span>
                </div>
              )}

              {(!video.visual_analysis_status || video.visual_analysis_status === 'pending' || video.visual_analysis_status === 'none') && (
                <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                  <p className="text-xs text-zinc-500 mb-2">Analyze video frames to detect objects, scenes, and visual content.</p>
                  <button
                    onClick={handleVisualAnalysis}
                    disabled={visualAnalysisLoading}
                    className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {visualAnalysisLoading ? <Loader2 size={12} className="animate-spin" /> : <Eye size={12} />}
                    Analyze
                  </button>
                  {visualAnalysisError && (
                    <p className="mt-2 text-[10px] text-red-400 flex items-center gap-1">
                      <AlertCircle size={10} />
                      {visualAnalysisError}
                    </p>
                  )}
                </div>
              )}

              {video.visual_analysis_status === 'failed' && (
                <div className="p-3 bg-red-500/5 border border-red-500/20 rounded-lg">
                  <p className="text-xs text-red-400 mb-2">Visual analysis failed.</p>
                  <button
                    onClick={handleVisualAnalysis}
                    disabled={visualAnalysisLoading}
                    className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {visualAnalysisLoading ? <Loader2 size={12} className="animate-spin" /> : <Eye size={12} />}
                    Retry
                  </button>
                </div>
              )}

              {video.visual_analysis_status === 'completed' && video.ai_analyze_text && (
                <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                  <p className="text-xs text-zinc-300 leading-relaxed whitespace-pre-wrap">
                    {video.ai_analyze_text}
                  </p>
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  );
};

export default VideoDetailPanel;
