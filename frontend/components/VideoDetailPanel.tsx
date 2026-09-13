import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  FileText, BookOpen, ScanEye, Eye, Loader2, Copy, Download, Check,
  Clock, Tag, ChevronRight, Brain, List, AlignLeft, ChevronDown, Music,
  PanelRightClose,
} from 'lucide-react';
import { Video, TranscriptData, SummaryData, Collection } from '../types';
import Loading from './common/Loading';
import { MediaCard } from './MediaCard';
import SodaLyricsTab from './SodaLyricsTab';
import { SpeakerChip } from './SpeakerChip';
import type { SodaTheme } from '../utils/sodaTheme';
import { isAudioType } from '../utils/awemeType';
import {
  triggerTranscription, triggerTranscriptionByResource,
  getTranscript, getTranscriptByResource,
  triggerSummary, triggerSummaryByResource,
  getSummary, getSummaryByResource,
  triggerVisualAnalysis, triggerVisualAnalysisByResource,
  getVisualAnalysisByResource,
  pollForResult,
} from '../services/aiService';
import type { VisualAnalysisData } from '../services/aiService';
import { useTaskManager } from '../contexts/TaskManagerContext';
import { TaskErrorNotice } from './TaskErrorNotice';

interface VideoDetailPanelProps {
  video: Video;
  resourceId?: string;
  /** Album detail only: the slide the viewer is showing. Forwarded to
   *  MediaCard so the Overview tab's Prompt block edits that slide's entry. */
  slide?: { name: string; index: number; count: number };
  onClose: () => void;
  onUpdate?: (id: string, updates: Partial<Video>) => void;
  onDelete?: (id: string, deleteFiles: boolean) => Promise<void>;
  collections?: Collection[];
  videoCollectionIds?: string[];
  onToggleCollection?: (collectionId: string) => void;
  onCreateCollection?: (name: string, teamId: string | null) => Promise<void>;
  /** Hide video preview in MediaCard (when external player is already shown) */
  hidePreview?: boolean;
  // Resource-level rating & notes
  resourceRating?: number;
  resourceNotes?: string;
  onRatingChange?: (rating: number) => void;
  onNotesChange?: (notes: string) => void;
  onNotesBlur?: () => void;
  /** Optional mobile action buttons rendered below the ID line */
  mobileActions?: React.ReactNode;
  /** Current playback position (seconds) — drives synced lyrics highlight. */
  playerCurrentTime?: number;
  /** Move the player to a point in the media, in seconds.
   *
   *  The transcript's timestamps advertise themselves as seek controls, so
   *  without this they are a control that lies: the button rendered, the
   *  tooltip said "Click to seek (coming soon)", and the click did nothing.
   *  The sibling surface (`ResourceDetailPage`) has always done the seek
   *  because it owns its own `<video>`; here the player lives in the parent
   *  page, so the capability has to arrive as a prop. Absent means the host
   *  has no player — the timestamps then render as plain text, not as dead
   *  buttons. */
  onSeek?: (seconds: number) => void;
  /** Track's own Soda palette — themes the lyrics tab for audio items. */
  sodaTheme?: SodaTheme;
  /**
   * Compact mode — forwarded to MediaCard. When true (mobile audio player),
   * the Overview tab hides AI intent badges, the action row, and description.
   * Defaults to false so desktop / mobile-video render identically.
   */
  compact?: boolean;
  /**
   * Island mode — when true, the panel renders content-only (tabs + body)
   * without its own outer width/positioning chrome. The island shell's
   * <aside className="island-card"> owns the width and scrolling. Adds a
   * collapse control to the tabs row. Defaults to false (classic split-pane).
   */
  island?: boolean;
  /** Called by the in-island collapse button (island mode only). */
  onCollapse?: () => void;
}

type TabKey = 'overview' | 'transcript' | 'analysis' | 'lyrics';

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
    const speaker = seg.speaker ? `[${seg.speaker}] ` : '';
    return `${i + 1}\n${formatSrtTime(seg.start)} --> ${formatSrtTime(seg.end)}\n${speaker}${seg.text}`;
  }).join('\n\n');
};

// Download text content as file (for SRT/TXT export)
const downloadTextFile = (content: string, filename: string, mimeType: string) => {
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

export const VideoDetailPanel: React.FC<VideoDetailPanelProps> = ({
  video,
  resourceId,
  slide,
  onClose,
  onUpdate,
  onDelete,
  collections,
  videoCollectionIds,
  onToggleCollection,
  onCreateCollection,
  hidePreview = false,
  resourceRating,
  resourceNotes,
  onRatingChange,
  onNotesChange,
  onNotesBlur,
  mobileActions,
  playerCurrentTime,
  onSeek,
  sodaTheme,
  compact = false,
  island = false,
  onCollapse,
}) => {
  // Island redesign: align neutral text/border to the mock --content/--line ladder.
  // Classic (island=false) keeps the exact original ink classes for D12 byte-identical render.
  const cText200 = island ? 'text-content' : 'text-ink-200';
  const cText300 = island ? 'text-content-2' : 'text-ink-300';
  const cText400 = island ? 'text-content-2' : 'text-ink-400';
  const cText500 = island ? 'text-content-3' : 'text-ink-500';
  const cBorder800 = island ? 'border-line' : 'border-ink-800';
  const cBorder700 = island ? 'border-line' : 'border-ink-700';
  const cHoverText200 = island ? 'hover:text-content' : 'hover:text-ink-200';
  const cHoverBorder700 = island ? 'hover:border-line' : 'hover:border-ink-700';
  const cDivide80050 = island ? 'divide-line/50' : 'divide-ink-800/50';
  // Surface backgrounds — map raw ink fills to the mock's semantic tokens.
  const cCardBg = island ? 'bg-card' : 'bg-ink-900';
  const cCtrlBg = island ? 'bg-island-2' : 'bg-ink-800';
  const cHoverSurf7 = island ? 'hover:bg-island-2' : 'hover:bg-ink-700';

  const [activeTab, setActiveTab] = useState<TabKey>('overview');
  const [transcript, setTranscript] = useState<TranscriptData | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [visualAnalysis, setVisualAnalysis] = useState<VisualAnalysisData | null>(null);
  // Task-driven phase for the Visual Analysis section — independent of
  // video.visual_analysis_status (the parent's onUpdate prop chain doesn't
  // reliably flow back; see the watcher effect below).
  const [analysisTaskPhase, setAnalysisTaskPhase] = useState<
    'processing' | 'completed' | 'failed' | null
  >(null);
  const analysisFetchAttemptedRef = useRef(false);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [visualAnalysisLoading, setVisualAnalysisLoading] = useState(false);
  const [visualAnalysisFetching, setVisualAnalysisFetching] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [visualAnalysisError, setVisualAnalysisError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [transcriptView, setTranscriptView] = useState<'segments' | 'fulltext'>('segments');
  const [exportOpen, setExportOpen] = useState(false);

  // Load existing transcript/summary when tab changes — always try to load
  useEffect(() => {
    if (activeTab === 'transcript' && !transcript && !transcriptLoading) {
      loadTranscript();
    }
    if (activeTab === 'analysis' && !summary && !summaryLoading) {
      loadSummary();
    }
    if (activeTab === 'analysis' && !visualAnalysis && !visualAnalysisFetching) {
      loadVisualAnalysis();
    }
  }, [activeTab]);

  const loadTranscript = useCallback(async () => {
    try {
      setTranscriptLoading(true);
      setTranscriptError(null);
      const data = resourceId
        ? await getTranscriptByResource(resourceId)
        : await getTranscript(video.platform_id);
      setTranscript(data);
    } catch {
      // 404 = no transcript yet — check if transcription is in progress
      if (resourceId) {
        try {
          const { getSupabaseClient } = await import('../supabaseClient');
          const supabase = getSupabaseClient();
          if (supabase) {
            const { data: tasks } = await supabase
              .from('task_tracking')
              .select('status')
              .eq('resource_id', resourceId)
              .eq('task_type', 'ai_transcription')
              .in('status', ['pending', 'processing', 'running'])
              .limit(1);
            if (tasks && tasks.length > 0) {
              // Active transcription task found — show processing and poll
              setTranscribeStatus('processing');
              const result = await pollForResult(
                () => getTranscriptByResource(resourceId!), 3000, 120
              );
              setTranscript(result);
              setTranscribeStatus('completed');
            } else if (video.transcript_status === 'failed') {
              // No active task + backend flagged the last attempt failed
              // (extract→transcribe chain or direct transcribe). Surface the
              // failure on tab re-open instead of silently showing the
              // Transcribe button as if nothing had happened. Pull the newest
              // related task's error_msg for a specific, humanized message.
              const { data: failedTasks } = await supabase
                .from('task_tracking')
                .select('error_msg')
                .eq('resource_id', resourceId)
                .in('task_type', ['ai_transcription', 'extract_audio'])
                .in('status', ['failed', 'cancelled'])
                .order('created_at', { ascending: false })
                .limit(1);
              setTranscribeStatus('failed');
              setTranscriptError(
                failedTasks?.[0]?.error_msg || 'Transcription failed'
              );
            }
          }
        } catch (err) {
          // best-effort failure surfacing — stay on empty state on error
          console.error('[VideoDetailPanel] transcript failure probe failed:', err);
        }
      }
    } finally {
      setTranscriptLoading(false);
    }
  }, [video.platform_id, resourceId]);

  const loadSummary = useCallback(async () => {
    try {
      setSummaryLoading(true);
      setSummaryError(null);
      const data = resourceId
        ? await getSummaryByResource(resourceId)
        : await getSummary(video.platform_id);
      setSummary(data);
    } catch {
      // 404 = no summary yet
    } finally {
      setSummaryLoading(false);
    }
  }, [video.platform_id, resourceId]);

  // The completed analysis lives in resource_analysis (read via
  // GET /ai/analysis/resource/:rid — same source as the Task Center result
  // card). The legacy parsed_media.ai_analyze_text column is never written by
  // the analyze_l1 workflow, so without this fetch the panel could never show
  // a result — it sat on the Trigger button forever even after success.
  const loadVisualAnalysis = useCallback(async () => {
    if (!resourceId) return;
    try {
      setVisualAnalysisFetching(true);
      const data = await getVisualAnalysisByResource(resourceId);
      // The endpoint deliberately returns 200 with null fields when the
      // resource was never analyzed (no 404, to avoid red DevTools rows on
      // every tab open) — presence is detected via visual_description.
      // Treating any 200 as "has analysis" rendered an EMPTY result card and
      // swallowed the Trigger button on un-analyzed videos.
      if (data.description) {
        setVisualAnalysis(data);
      }
    } catch {
      // network error — stay on the trigger/processing state
    } finally {
      setVisualAnalysisFetching(false);
    }
  }, [resourceId]);

  const [transcribeStatus, setTranscribeStatus] = useState<string | null>(null);
  const { tasks } = useTaskManager();

  // Short-circuit polling when the backend transcription hits a terminal
  // failure. Without this the UI sat on "Processing…" for the full 3-minute
  // pollForResult window even though task_tracking already had phase='failed'.
  //
  // Includes ``extract_audio``: a manual Transcribe click on a video with no
  // extracted audio dispatches extract_audio(chain_transcription=True), so the
  // task that FAILS (e.g. ffmpeg "no audio track") is the extract_audio one —
  // never an ai_transcription task. Matching only ai_transcription left the
  // spinner running the full window on exactly the no-audio case this fixes.
  useEffect(() => {
    if (!resourceId) return;
    if (transcribeStatus !== 'processing') return;
    const failed = tasks.find(
      (t) => (t.task_type === 'ai_transcription' || t.task_type === 'extract_audio')
        && String(t.resource_id) === String(resourceId)
        && (t.status === 'failed' || t.status === 'cancelled')
    );
    if (failed) {
      setTranscribeStatus('failed');
      setTranscriptError(failed.error_msg || 'Transcription failed');
      setTranscriptLoading(false);
    }
  }, [tasks, resourceId, transcribeStatus]);

  // Visual analysis is TASK-DRIVEN: the panel mirrors the latest ai_extract
  // task for this resource into LOCAL state (analysisTaskPhase) and fetches
  // the result on completion. It deliberately does NOT gate on
  // video.visual_analysis_status — the earlier version did
  // (`if (status !== 'processing') return`), relying on onUpdate writing
  // 'processing' back into the video prop, but DownloadDetailPage's prop
  // chain doesn't flow updates back, so the watcher never fired: the run
  // completed, the result row existed, and the panel never fetched it
  // (api_request_logs showed zero GETs after completion).
  useEffect(() => {
    if (!resourceId) return;
    const latest = tasks
      .filter(
        (t) =>
          t.task_type === 'ai_extract'
          && String(t.resource_id) === String(resourceId),
      )
      .sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      )[0];
    if (!latest) return;
    if (latest.status === 'completed') {
      setAnalysisTaskPhase('completed');
      setVisualAnalysisLoading(false);
      if (!visualAnalysis && !analysisFetchAttemptedRef.current) {
        analysisFetchAttemptedRef.current = true;
        loadVisualAnalysis();
      }
      if (onUpdate && video.visual_analysis_status === 'processing') {
        onUpdate(video.platform_id, { visual_analysis_status: 'completed' });
      }
    } else if (latest.status === 'failed' || latest.status === 'cancelled') {
      setAnalysisTaskPhase('failed');
      setVisualAnalysisError(latest.error_msg || 'Visual analysis failed');
      setVisualAnalysisLoading(false);
    } else {
      // queued / processing — a retry resets the fetch guard so the NEXT
      // completion fetches fresh data.
      setAnalysisTaskPhase('processing');
      analysisFetchAttemptedRef.current = false;
    }
  }, [
    tasks, resourceId, video.visual_analysis_status, video.platform_id,
    onUpdate, visualAnalysis, loadVisualAnalysis,
  ]);

  const handleTranscribe = async () => {
    try {
      setTranscriptLoading(true);
      setTranscriptError(null);
      setTranscribeStatus('processing');
      if (resourceId) {
        await triggerTranscriptionByResource(resourceId);
        const data = await pollForResult(() => getTranscriptByResource(resourceId), 3000, 60);
        setTranscript(data);
      } else {
        await triggerTranscription(video.platform_id);
        const data = await pollForResult(() => getTranscript(video.platform_id), 3000, 60);
        setTranscript(data);
      }
      setTranscribeStatus('completed');
      if (onUpdate) {
        onUpdate(video.platform_id, { transcript_status: 'completed' });
      }
    } catch (err) {
      setTranscriptError(err instanceof Error ? err.message : 'Failed to transcribe');
      setTranscribeStatus('failed');
    } finally {
      setTranscriptLoading(false);
    }
  };

  const handleSummarize = async () => {
    try {
      setSummaryLoading(true);
      setSummaryError(null);
      if (resourceId) {
        await triggerSummaryByResource(resourceId);
        const data = await pollForResult(() => getSummaryByResource(resourceId), 3000, 60);
        setSummary(data);
      } else {
        await triggerSummary(video.platform_id);
        const data = await pollForResult(() => getSummary(video.platform_id), 3000, 60);
        setSummary(data);
      }
      if (onUpdate) {
        onUpdate(video.platform_id, { summary_status: 'completed' });
      }
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : 'Failed to summarize');
    } finally {
      setSummaryLoading(false);
    }
  };

  const handleVisualAnalysis = async () => {
    try {
      setVisualAnalysisLoading(true);
      setVisualAnalysisError(null);
      // Prefer the resource-based trigger (dispatches analyze_l1_workflow) —
      // same migration transcript/summary already got. The platform_id path
      // (triggerVisualAnalysis) is the legacy 501 "not implemented" stub.
      if (resourceId) {
        await triggerVisualAnalysisByResource(resourceId);
      } else {
        await triggerVisualAnalysis(video.platform_id);
      }
      // Optimistic local phase — the task row arrives via realtime a beat
      // later; until then the spinner (not the trigger button) should show.
      setAnalysisTaskPhase('processing');
      analysisFetchAttemptedRef.current = false;
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
      .map((s) => {
        const speaker = s.speaker ? `[${s.speaker}] ` : '';
        return `[${formatTimestamp(s.start)}] ${speaker}${s.text}`;
      })
      .join('\n');
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleExportSRT = () => {
    if (!transcript) return;
    const srt = generateSRT(transcript.segments);
    downloadTextFile(srt, `${video.platform_id}_transcript.srt`, 'text/srt');
  };

  const handleExportTXT = () => {
    if (!transcript) return;
    downloadTextFile(transcript.text, `${video.platform_id}_transcript.txt`, 'text/plain');
  };

  const isAudio = isAudioType(video.media_type);

  const tabs: { key: TabKey; label: string; icon: React.ReactNode }[] = [
    { key: 'overview', label: 'Overview', icon: <Eye size={16} /> },
    { key: 'transcript', label: 'Transcript', icon: <FileText size={16} /> },
    { key: 'analysis', label: 'Analysis', icon: <ScanEye size={16} /> },
    { key: 'lyrics', label: 'Lyrics', icon: <Music size={16} /> },
  ];

  // Audio items show only Overview + Lyrics (no transcript / analysis).
  // Non-audio items keep the original tabs and never show a Lyrics tab.
  const visibleTabs = isAudio
    ? tabs.filter((t) => t.key === 'overview' || t.key === 'lyrics')
    : tabs.filter((t) => t.key !== 'lyrics');

  // Guard: if the active tab is no longer visible (e.g. switching to an audio
  // item while on 'transcript'), fall back to 'overview' so nothing renders blank.
  useEffect(() => {
    if (!visibleTabs.some((t) => t.key === activeTab)) {
      setActiveTab('overview');
    }
  }, [isAudio]);

  const getStatusIndicator = (status?: string) => {
    switch (status) {
      case 'processing':
        return <span className="w-2 h-2 rounded-full bg-info animate-pulse" />;
      case 'completed':
        return <span className="w-2 h-2 rounded-full bg-ok" />;
      case 'failed':
        return <span className="w-2 h-2 rounded-full bg-red-400" />;
      default:
        return null;
    }
  };

  return (
    <div className={island ? 'h-full flex flex-col min-h-0' : 'flex flex-col h-full'}>
      {/* Tab Navigation */}
      <div className={`flex border-b ${cBorder800} mb-4 shrink-0`}>
        {visibleTabs.map((tab) => {
          const status = tab.key === 'transcript'
            ? video.transcript_status
            : tab.key === 'analysis'
            ? video.summary_status
            : undefined;

          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === tab.key
                  ? 'border-accent text-[var(--accent-text)]'
                  : `border-transparent ${cText400} ${cHoverText200} ${cHoverBorder700}`
              }`}
            >
              {tab.icon}
              {tab.label}
              {getStatusIndicator(status)}
            </button>
          );
        })}
        {island && (
          <button
            onClick={onCollapse}
            aria-label="Collapse panel"
            title="Collapse panel"
            className={`ml-auto px-3 py-3 ${cText400} ${cHoverText200} transition-colors`}
          >
            <PanelRightClose size={16} />
          </button>
        )}
      </div>

      {/* Tab Content */}
      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
        {/* Overview Tab */}
        {activeTab === 'overview' && (
          <MediaCard
            data={video}
            onUpdate={onUpdate}
            onDelete={onDelete}
            collections={collections}
            videoCollectionIds={videoCollectionIds}
            onToggleCollection={onToggleCollection}
            onCreateCollection={onCreateCollection}
            hidePreview={hidePreview}
            resourceRating={resourceRating}
            resourceNotes={resourceNotes}
            onRatingChange={onRatingChange}
            onNotesChange={onNotesChange}
            onNotesBlur={onNotesBlur}
            mobileActions={mobileActions}
            compact={compact}
            bare={island}
            slide={slide}
          />
        )}

        {/* Lyrics Tab (audio items only) */}
        {activeTab === 'lyrics' && (
          <div className="animate-in fade-in duration-300">
            <SodaLyricsTab mediaId={String(video.id)} currentTime={playerCurrentTime} theme={sodaTheme} sourcePlatform={video.source_platform} />
          </div>
        )}

        {/* Transcript Tab */}
        {activeTab === 'transcript' && (
          <div className="space-y-4 animate-in fade-in duration-300">
            {/* Processing state — active transcription in progress */}
            {transcribeStatus === 'processing' && !transcript && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className="relative mb-6">
                  <div className="w-16 h-16 rounded-full border-2 border-accent/20" />
                  <div className="absolute inset-0 w-16 h-16 rounded-full border-2 border-transparent border-t-accent animate-spin" />
                  <div className="absolute inset-0 flex items-center justify-center">
                    <Brain size={24} className="text-[var(--accent-text)]" />
                  </div>
                </div>
                <h3 className={`text-base font-medium ${cText200}`}>Transcribing Audio...</h3>
                <p className={`text-sm ${cText500} mt-2 max-w-[280px]`}>
                  AI is processing the audio. This may take a few minutes depending on the length.
                </p>
                <div className="mt-4 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-info animate-pulse" />
                  <span className="text-xs text-[var(--accent-text)]">Processing</span>
                </div>
              </div>
            )}

            {/* Loading existing transcript from server */}
            {transcriptLoading && transcribeStatus !== 'processing' && !transcript && (
              <div className="flex flex-col items-center justify-center py-16 text-[var(--accent-text)]">
                <Loading center label="Loading transcript..." />
              </div>
            )}

            {/* Not started — no transcript and not loading/processing */}
            {!transcript && !transcriptLoading && transcribeStatus !== 'processing' && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className={`p-4 ${island ? 'bg-island-2' : 'bg-ink-800/50'} rounded-full mb-4`}>
                  <FileText size={32} className={cText500} />
                </div>
                <h3 className={`text-lg font-medium ${cText200}`}>No Transcript Available</h3>
                <p className={`text-sm ${cText500} mt-1 mb-6 max-w-md`}>
                  Generate a transcript to see timestamped text from this video's audio.
                </p>
                <button
                  onClick={handleTranscribe}
                  disabled={transcriptLoading}
                  className="px-6 py-3 bg-accent hover:opacity-90 text-white rounded-lg font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                >
                  <Brain size={18} />
                  Transcribe
                </button>
                {transcriptError && (
                  <TaskErrorNotice error={transcriptError} size="sm" className="mt-4" />
                )}
              </div>
            )}

            {/* Transcript content.
                Three things this layout is answering, all visible in the
                surface it replaces:

                 * NO SECOND SCROLLER. The list used to sit in its own
                   `max-h-[50vh] overflow-y-auto` box INSIDE the panel's own
                   `flex-1 min-h-0 overflow-y-auto` body. Two nested scrollers
                   fight the wheel, and capping at half the viewport left the
                   controls stranded mid-page above a screen of dead space.
                   One scroller now — the panel's — and the list is as long as
                   the transcript is.
                 * CONTROLS ABOVE THE TEXT, AND STICKY. Once the list is not
                   capped, a toolbar below it is 114 segments away. Meta and
                   controls describe the same object, so they share one row
                   that stays put while you read.
                 * THE SPEAKER CHIP MARKS A CHANGE, NOT A ROW. Stamping S02 on
                   every one of 114 consecutive segments is the same as
                   stamping none: the chip only earns its space where the
                   speaker actually turns over. */}
            {transcript && (
              <div className="space-y-2">
                {/* One chrome row: what this transcript is, and what you can
                    do to it. Sticky against the panel body's scroller. */}
                <div className={`sticky top-0 z-10 -mx-1 flex flex-wrap items-center gap-x-3 gap-y-2 px-1 py-2 ${island ? 'bg-island-1' : 'bg-ink-900'}`}>
                  <div className={`flex items-center gap-3 text-xs ${cText500}`}>
                    <span className="flex items-center gap-1">
                      <Clock size={12} />
                      {formatTimestamp(transcript.duration)}
                    </span>
                    <span className="flex items-center gap-1">
                      <Tag size={12} />
                      {transcript.language.toUpperCase()}
                    </span>
                    <span className="tabular-nums">{transcript.segments.length} segments</span>
                  </div>

                  <span className="flex-1" />

                  <div className="flex items-center gap-2">
                    <div className={`flex ${cCtrlBg} border ${cBorder700} rounded-lg overflow-hidden`}>
                      <button
                        onClick={() => setTranscriptView('segments')}
                        aria-pressed={transcriptView === 'segments'}
                        className={`px-3 py-1.5 text-xs flex items-center gap-1.5 transition-colors ${
                          transcriptView === 'segments'
                            ? 'bg-accent text-white'
                            : `${cText400} ${cHoverText200}`
                        }`}
                      >
                        <List size={12} />
                        Segments
                      </button>
                      <button
                        onClick={() => setTranscriptView('fulltext')}
                        aria-pressed={transcriptView === 'fulltext'}
                        className={`px-3 py-1.5 text-xs flex items-center gap-1.5 transition-colors ${
                          transcriptView === 'fulltext'
                            ? 'bg-accent text-white'
                            : `${cText400} ${cHoverText200}`
                        }`}
                      >
                        <AlignLeft size={12} />
                        Full Text
                      </button>
                    </div>

                    <button
                      onClick={handleCopyTranscript}
                      className={`px-3 py-1.5 text-xs ${cCtrlBg} ${cHoverSurf7} ${cText300} rounded-lg transition-colors flex items-center gap-1.5 border ${cBorder700}`}
                    >
                      {copied ? <Check size={12} className="text-ok" /> : <Copy size={12} />}
                      {copied ? 'Copied' : 'Copy'}
                    </button>

                    <div className="relative">
                      <button
                        onClick={() => setExportOpen(!exportOpen)}
                        aria-expanded={exportOpen}
                        className={`px-3 py-1.5 text-xs ${cCtrlBg} ${cHoverSurf7} ${cText300} rounded-lg transition-colors flex items-center gap-1.5 border ${cBorder700}`}
                      >
                        <Download size={12} />
                        Export
                        <ChevronDown size={10} />
                      </button>
                      {exportOpen && (
                        /* Opens DOWNWARD now that the toolbar sits at the top;
                           `bottom-full` here would fly off the panel. */
                        <div className={`absolute top-full mt-1 right-0 ${cCtrlBg} border ${cBorder700} rounded-lg shadow-xl overflow-hidden z-20 min-w-[120px]`}>
                          <button
                            onClick={() => { handleExportSRT(); setExportOpen(false); }}
                            className={`w-full px-3 py-2 text-xs ${cText300} ${cHoverSurf7} text-left transition-colors`}
                          >
                            Export SRT
                          </button>
                          <button
                            onClick={() => { handleExportTXT(); setExportOpen(false); }}
                            className={`w-full px-3 py-2 text-xs ${cText300} ${cHoverSurf7} text-left transition-colors`}
                          >
                            Export TXT
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <div className={`${cCardBg} border ${cBorder800} rounded-xl overflow-hidden`}>
                  {transcriptView === 'segments' ? (
                    <div className={`divide-y ${cDivide80050}`}>
                      {transcript.segments.map((seg, i) => {
                        const prev = transcript.segments[i - 1];
                        const speakerTurns = !!seg.speaker && seg.speaker !== prev?.speaker;
                        const playing =
                          playerCurrentTime !== undefined &&
                          playerCurrentTime >= seg.start &&
                          playerCurrentTime < seg.end;
                        return (
                          <div
                            key={i}
                            data-playing={playing || undefined}
                            className={`flex gap-3 px-4 py-2.5 transition-colors ${
                              playing
                                ? 'bg-[var(--accent-soft)]'
                                : island
                                  ? 'hover:bg-island-2'
                                  : 'hover:bg-ink-800/30'
                            }`}
                          >
                            {/* A fixed mono gutter, so 114 timecodes line up as
                                a column instead of ragging against the text.
                                The brackets are gone — a right-aligned mono
                                column already reads as a timecode, and they
                                were two characters of chrome per row.

                                Only a button when it can actually do
                                something: with no player behind the panel this
                                renders as plain text rather than as a control
                                that does nothing when clicked. */}
                            {onSeek ? (
                              <button
                                type="button"
                                onClick={() => onSeek(seg.start)}
                                title={`Play from ${formatTimestamp(seg.start)}`}
                                className={`w-11 shrink-0 pt-0.5 text-right font-mono text-xs tabular-nums transition-colors ${
                                  playing ? 'text-[var(--accent-text)]' : `${cText500} hover:text-[var(--accent-text)]`
                                }`}
                              >
                                {formatTimestamp(seg.start)}
                              </button>
                            ) : (
                              <span className={`w-11 shrink-0 pt-0.5 text-right font-mono text-xs tabular-nums ${cText500}`}>
                                {formatTimestamp(seg.start)}
                              </span>
                            )}

                            <div className="min-w-0 flex-1">
                              {speakerTurns && (
                                <div className="mb-1">
                                  <SpeakerChip speaker={seg.speaker} />
                                </div>
                              )}
                              <p className={`text-sm ${cText300} leading-relaxed`}>{seg.text}</p>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="p-4">
                      <p className={`text-sm ${cText300} leading-relaxed whitespace-pre-wrap`}>
                        {transcript.text}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Analysis Tab */}
        {activeTab === 'analysis' && (
          <div className="space-y-6 animate-in fade-in duration-300">
            {/* Summary Section */}
            <section className="space-y-3">
              <div className="flex items-center gap-2">
                <div className="p-1.5 bg-[var(--accent-soft)] rounded-lg text-[var(--accent-text)]">
                  <BookOpen size={16} />
                </div>
                <h3 className={`font-medium ${cText200}`}>Summary</h3>
                {getStatusIndicator(video.summary_status)}
              </div>

              {/* Summary processing */}
              {video.summary_status === 'processing' && (
                <div className={`flex items-center gap-3 p-4 ${cCardBg} border ${cBorder800} rounded-lg`}>
                  <Loader2 size={18} className="animate-spin text-[var(--accent-text)]" />
                  <span className={`text-sm ${cText400}`}>Generating summary...</span>
                </div>
              )}

              {/* Summary not started */}
              {(!video.summary_status || video.summary_status === 'pending') && !summary && (
                <div className={`p-4 ${cCardBg} border ${cBorder800} rounded-lg`}>
                  <p className={`text-sm ${cText500} mb-3`}>
                    Generate an AI summary with key points and topics.
                  </p>
                  <button
                    onClick={handleSummarize}
                    disabled={summaryLoading}
                    className="px-4 py-2 bg-accent hover:opacity-90 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    {summaryLoading ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <BookOpen size={14} />
                    )}
                    Summarize
                  </button>
                  {summaryError && (
                    <TaskErrorNotice error={summaryError} size="xs" className="mt-2" />
                  )}
                </div>
              )}

              {/* Summary failed */}
              {video.summary_status === 'failed' && !summary && (
                <div className="p-4 bg-red-500/5 border border-red-500/20 rounded-lg">
                  <p className="text-sm text-red-400 mb-3">Summarization failed. Please try again.</p>
                  <button
                    onClick={handleSummarize}
                    disabled={summaryLoading}
                    className="px-4 py-2 bg-accent hover:opacity-90 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    {summaryLoading ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <BookOpen size={14} />
                    )}
                    Retry
                  </button>
                </div>
              )}

              {/* Summary loading */}
              {summaryLoading && video.summary_status === 'completed' && (
                <div className="flex items-center justify-center py-8 text-[var(--accent-text)]">
                  <Loading center />
                </div>
              )}

              {/* Summary content */}
              {summary && (
                <div className="space-y-4">
                  {/* Summary text */}
                  <div className={`p-4 ${cCardBg} border ${cBorder800} rounded-lg`}>
                    <p className={`text-sm ${cText300} leading-relaxed`}>{summary.summary}</p>
                  </div>

                  {/* Key points */}
                  {summary.key_points.length > 0 && (
                    <div>
                      <h4 className={`text-xs font-medium ${cText400} uppercase tracking-wider mb-2`}>
                        Key Points
                      </h4>
                      <ul className="space-y-2">
                        {summary.key_points.map((point, i) => (
                          <li key={i} className={`flex items-start gap-2 text-sm ${cText300}`}>
                            <ChevronRight size={14} className="text-[var(--accent-text)] mt-0.5 shrink-0" />
                            {point}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Topics */}
                  {summary.topics.length > 0 && (
                    <div>
                      <h4 className={`text-xs font-medium ${cText400} uppercase tracking-wider mb-2`}>
                        Topics
                      </h4>
                      <div className="flex flex-wrap gap-2">
                        {summary.topics.map((topic, i) => {
                          const colors = [
                            'bg-info-soft text-info border-info-line',
                            'bg-ok-soft text-ok border-ok-line',
                            'bg-purple-500/10 text-purple-400 border-purple-500/20',
                            'bg-amber-500/10 text-amber-400 border-amber-500/20',
                            'bg-rose-500/10 text-rose-400 border-rose-500/20',
                            'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
                          ];
                          return (
                            <span
                              key={i}
                              className={`px-2.5 py-1 rounded-full text-xs font-medium border ${colors[i % colors.length]}`}
                            >
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
            <section className="space-y-3">
              <div className="flex items-center gap-2">
                <div className="p-1.5 bg-purple-500/10 rounded-lg text-purple-400">
                  <ScanEye size={16} />
                </div>
                <h3 className={`font-medium ${cText200}`}>Visual Analysis</h3>
                {getStatusIndicator(video.visual_analysis_status)}
              </div>

              {/* Result card — fetched from resource_analysis, the table the
                  analyze_l1 workflow actually writes. Takes priority over the
                  status branches: fetched data is ground truth. */}
              {visualAnalysis && (
                <div className={`p-4 ${cCardBg} border ${cBorder800} rounded-lg space-y-3`}>
                  {visualAnalysis.description && (
                    <p className={`text-sm ${cText300} leading-relaxed whitespace-pre-wrap`}>
                      {visualAnalysis.description}
                    </p>
                  )}
                  {(() => {
                    const chips = [
                      ...(visualAnalysis.objects ?? []),
                      ...(visualAnalysis.scenes ?? []),
                      ...(visualAnalysis.people ?? []),
                    ].filter(Boolean);
                    return chips.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {chips.map((c, i) => (
                          <span
                            key={i}
                            className={`px-2 py-0.5 rounded-full text-[10px] ${cCtrlBg} ${cText300}`}
                          >
                            {String(c)}
                          </span>
                        ))}
                      </div>
                    ) : null;
                  })()}
                  {visualAnalysis.text && (
                    <p className={`text-xs ${cText400} whitespace-pre-wrap break-words bg-ink-950/40 rounded p-2 border ${cBorder800}`}>
                      {visualAnalysis.text}
                    </p>
                  )}
                  {visualAnalysis.model && (
                    <p className={`text-[11px] ${cText500}`}>{visualAnalysis.model}</p>
                  )}
                </div>
              )}

              {!visualAnalysis
                && (analysisTaskPhase === 'processing'
                  || (analysisTaskPhase === null
                    && video.visual_analysis_status === 'processing')) && (
                <div className={`flex items-center gap-3 p-4 ${cCardBg} border ${cBorder800} rounded-lg`}>
                  <Loader2 size={18} className="animate-spin text-purple-400" />
                  <span className={`text-sm ${cText400}`}>Analyzing visual content...</span>
                </div>
              )}

              {/* Trigger is the catch-all: anything that's not actively
                  processing/failed and has no fetched result shows the button.
                  An allowlist here broke twice — the column's DB default is
                  'none' (not 'pending'; ResourceDetailPage checks it
                  explicitly), and unknown future values would blank the
                  section again. analysisTaskPhase (task-driven local state)
                  takes precedence over the video prop, whose updates don't
                  reliably flow back from the parent. */}
              {!visualAnalysis
                && analysisTaskPhase !== 'processing'
                && analysisTaskPhase !== 'failed'
                && !(analysisTaskPhase === null
                  && (video.visual_analysis_status === 'processing'
                    || video.visual_analysis_status === 'failed')) && (
                <div className={`p-4 ${cCardBg} border ${cBorder800} rounded-lg`}>
                  {visualAnalysisFetching ? (
                    <Loading label="Loading analysis..." className="text-purple-400" />
                  ) : (
                    <>
                      <p className={`text-sm ${cText500} mb-3`}>
                        Analyze video frames to detect objects, scenes, and visual content.
                      </p>
                      <button
                        onClick={handleVisualAnalysis}
                        disabled={visualAnalysisLoading}
                        className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                      >
                        {visualAnalysisLoading ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : (
                          <ScanEye size={14} />
                        )}
                        Trigger Visual Analysis
                      </button>
                      {visualAnalysisError && (
                        <TaskErrorNotice error={visualAnalysisError} size="xs" className="mt-2" />
                      )}
                    </>
                  )}
                </div>
              )}

              {!visualAnalysis
                && (analysisTaskPhase === 'failed'
                  || (analysisTaskPhase === null
                    && video.visual_analysis_status === 'failed')) && (
                <div className="p-4 bg-red-500/5 border border-red-500/20 rounded-lg">
                  <p className="text-sm text-red-400 mb-3">Visual analysis failed. Please try again.</p>
                  <button
                    onClick={handleVisualAnalysis}
                    disabled={visualAnalysisLoading}
                    className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    {visualAnalysisLoading ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <ScanEye size={14} />
                    )}
                    Retry
                  </button>
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
