import React, { useState, useEffect, useCallback } from 'react';
import {
  FileText, Sparkles, Eye, Loader2, Copy, Download, Check,
  Clock, Tag, ChevronRight, Brain, AlertCircle, List, AlignLeft, ChevronDown,
} from 'lucide-react';
import { Video, TranscriptData, SummaryData, Collection } from '../types';
import { MediaCard } from './MediaCard';
import {
  triggerTranscription, triggerTranscriptionByResource,
  getTranscript, getTranscriptByResource,
  triggerSummary, triggerSummaryByResource,
  getSummary, getSummaryByResource,
  triggerVisualAnalysis,
  pollForResult,
} from '../services/aiService';

interface VideoDetailPanelProps {
  video: Video;
  resourceId?: string;
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
}

type TabKey = 'overview' | 'transcript' | 'analysis';

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
}) => {
  const [activeTab, setActiveTab] = useState<TabKey>('overview');
  const [transcript, setTranscript] = useState<TranscriptData | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [visualAnalysisLoading, setVisualAnalysisLoading] = useState(false);
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
            }
          }
        } catch {
          // ignore — just stay on empty state
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

  const [transcribeStatus, setTranscribeStatus] = useState<string | null>(null);

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
    downloadTextFile(srt, `${video.platform_id}_transcript.srt`, 'text/srt');
  };

  const handleExportTXT = () => {
    if (!transcript) return;
    downloadTextFile(transcript.text, `${video.platform_id}_transcript.txt`, 'text/plain');
  };

  const tabs: { key: TabKey; label: string; icon: React.ReactNode }[] = [
    { key: 'overview', label: 'Overview', icon: <Eye size={16} /> },
    { key: 'transcript', label: 'Transcript', icon: <FileText size={16} /> },
    { key: 'analysis', label: 'Analysis', icon: <Sparkles size={16} /> },
  ];

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

  return (
    <div className="flex flex-col h-full">
      {/* Tab Navigation */}
      <div className="flex border-b border-zinc-800 mb-4 shrink-0">
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
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === tab.key
                  ? 'border-indigo-500 text-indigo-400'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700'
              }`}
            >
              {tab.icon}
              {tab.label}
              {getStatusIndicator(status)}
            </button>
          );
        })}
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
          />
        )}

        {/* Transcript Tab */}
        {activeTab === 'transcript' && (
          <div className="space-y-4 animate-in fade-in duration-300">
            {/* Processing state — active transcription in progress */}
            {transcribeStatus === 'processing' && !transcript && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className="relative mb-6">
                  <div className="w-16 h-16 rounded-full border-2 border-indigo-500/20" />
                  <div className="absolute inset-0 w-16 h-16 rounded-full border-2 border-transparent border-t-indigo-500 animate-spin" />
                  <div className="absolute inset-0 flex items-center justify-center">
                    <Brain size={24} className="text-indigo-400" />
                  </div>
                </div>
                <h3 className="text-base font-medium text-zinc-200">Transcribing Audio...</h3>
                <p className="text-sm text-zinc-500 mt-2 max-w-[280px]">
                  AI is processing the audio. This may take a few minutes depending on the length.
                </p>
                <div className="mt-4 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
                  <span className="text-xs text-indigo-400/70">Processing</span>
                </div>
              </div>
            )}

            {/* Loading existing transcript from server */}
            {transcriptLoading && transcribeStatus !== 'processing' && !transcript && (
              <div className="flex flex-col items-center justify-center py-16">
                <Loader2 size={24} className="animate-spin text-indigo-400 mb-3" />
                <p className="text-xs text-zinc-500">Loading transcript...</p>
              </div>
            )}

            {/* Not started — no transcript and not loading/processing */}
            {!transcript && !transcriptLoading && transcribeStatus !== 'processing' && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className="p-4 bg-zinc-800/50 rounded-full mb-4">
                  <FileText size={32} className="text-zinc-500" />
                </div>
                <h3 className="text-lg font-medium text-zinc-200">No Transcript Available</h3>
                <p className="text-sm text-zinc-500 mt-1 mb-6 max-w-md">
                  Generate a transcript to see timestamped text from this video's audio.
                </p>
                <button
                  onClick={handleTranscribe}
                  disabled={transcriptLoading}
                  className="px-6 py-3 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                >
                  <Brain size={18} />
                  Transcribe
                </button>
                {transcriptError && (
                  <div className="mt-4 flex items-center gap-2 text-sm text-red-400">
                    <AlertCircle size={14} />
                    {transcriptError}
                  </div>
                )}
              </div>
            )}

            {/* Transcript content */}
            {transcript && (
              <div className="space-y-4">
                {/* Meta info */}
                <div className="flex flex-wrap items-center gap-3 text-xs text-zinc-500">
                  <span className="flex items-center gap-1">
                    <Clock size={12} />
                    {formatTimestamp(transcript.duration)} total
                  </span>
                  <span className="flex items-center gap-1">
                    <Tag size={12} />
                    {transcript.language.toUpperCase()}
                  </span>
                  <span>{transcript.segments.length} segments</span>
                </div>

                {/* Content area */}
                <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
                  <div className="max-h-[50vh] overflow-y-auto custom-scrollbar">
                    {transcriptView === 'segments' ? (
                      <div className="divide-y divide-zinc-800/50">
                        {transcript.segments.map((seg, i) => (
                          <div
                            key={i}
                            className="flex gap-3 px-4 py-3 hover:bg-zinc-800/30 transition-colors group"
                          >
                            <button
                              className="text-xs font-mono text-indigo-400/70 group-hover:text-indigo-400 shrink-0 pt-0.5 transition-colors"
                              title="Click to seek (coming soon)"
                            >
                              [{formatTimestamp(seg.start)}]
                            </button>
                            <p className="text-sm text-zinc-300 leading-relaxed">{seg.text}</p>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="p-4">
                        <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                          {transcript.text}
                        </p>
                      </div>
                    )}
                  </div>
                </div>

                {/* Toolbar */}
                <div className="flex items-center gap-2">
                  {/* View toggle */}
                  <div className="flex bg-zinc-800 border border-zinc-700 rounded-lg overflow-hidden">
                    <button
                      onClick={() => setTranscriptView('segments')}
                      className={`px-3 py-1.5 text-xs flex items-center gap-1.5 transition-colors ${
                        transcriptView === 'segments'
                          ? 'bg-indigo-600 text-white'
                          : 'text-zinc-400 hover:text-zinc-200'
                      }`}
                    >
                      <List size={12} />
                      Segments
                    </button>
                    <button
                      onClick={() => setTranscriptView('fulltext')}
                      className={`px-3 py-1.5 text-xs flex items-center gap-1.5 transition-colors ${
                        transcriptView === 'fulltext'
                          ? 'bg-indigo-600 text-white'
                          : 'text-zinc-400 hover:text-zinc-200'
                      }`}
                    >
                      <AlignLeft size={12} />
                      Full Text
                    </button>
                  </div>

                  {/* Copy */}
                  <button
                    onClick={handleCopyTranscript}
                    className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors flex items-center gap-1.5 border border-zinc-700"
                  >
                    {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
                    {copied ? 'Copied!' : 'Copy'}
                  </button>

                  {/* Export dropdown */}
                  <div className="relative">
                    <button
                      onClick={() => setExportOpen(!exportOpen)}
                      className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors flex items-center gap-1.5 border border-zinc-700"
                    >
                      <Download size={12} />
                      Export
                      <ChevronDown size={10} />
                    </button>
                    {exportOpen && (
                      <div className="absolute bottom-full mb-1 left-0 bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl overflow-hidden z-10 min-w-[120px]">
                        <button
                          onClick={() => { handleExportSRT(); setExportOpen(false); }}
                          className="w-full px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-700 text-left transition-colors"
                        >
                          Export SRT
                        </button>
                        <button
                          onClick={() => { handleExportTXT(); setExportOpen(false); }}
                          className="w-full px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-700 text-left transition-colors"
                        >
                          Export TXT
                        </button>
                      </div>
                    )}
                  </div>
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
                <div className="p-1.5 bg-indigo-500/10 rounded-lg text-indigo-400">
                  <Sparkles size={16} />
                </div>
                <h3 className="font-medium text-zinc-200">Summary</h3>
                {getStatusIndicator(video.summary_status)}
              </div>

              {/* Summary processing */}
              {video.summary_status === 'processing' && (
                <div className="flex items-center gap-3 p-4 bg-zinc-900 border border-zinc-800 rounded-lg">
                  <Loader2 size={18} className="animate-spin text-indigo-400" />
                  <span className="text-sm text-zinc-400">Generating summary...</span>
                </div>
              )}

              {/* Summary not started */}
              {(!video.summary_status || video.summary_status === 'pending') && !summary && (
                <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-lg">
                  <p className="text-sm text-zinc-500 mb-3">
                    Generate an AI summary with key points and topics.
                  </p>
                  <button
                    onClick={handleSummarize}
                    disabled={summaryLoading}
                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    {summaryLoading ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Sparkles size={14} />
                    )}
                    Summarize
                  </button>
                  {summaryError && (
                    <p className="mt-2 text-xs text-red-400 flex items-center gap-1">
                      <AlertCircle size={12} />
                      {summaryError}
                    </p>
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
                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    {summaryLoading ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Sparkles size={14} />
                    )}
                    Retry
                  </button>
                </div>
              )}

              {/* Summary loading */}
              {summaryLoading && video.summary_status === 'completed' && (
                <div className="flex items-center justify-center py-8">
                  <Loader2 size={20} className="animate-spin text-indigo-400" />
                </div>
              )}

              {/* Summary content */}
              {summary && (
                <div className="space-y-4">
                  {/* Summary text */}
                  <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-lg">
                    <p className="text-sm text-zinc-300 leading-relaxed">{summary.summary}</p>
                  </div>

                  {/* Key points */}
                  {summary.key_points.length > 0 && (
                    <div>
                      <h4 className="text-xs font-medium text-zinc-400 uppercase tracking-wider mb-2">
                        Key Points
                      </h4>
                      <ul className="space-y-2">
                        {summary.key_points.map((point, i) => (
                          <li key={i} className="flex items-start gap-2 text-sm text-zinc-300">
                            <ChevronRight size={14} className="text-indigo-400 mt-0.5 shrink-0" />
                            {point}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Topics */}
                  {summary.topics.length > 0 && (
                    <div>
                      <h4 className="text-xs font-medium text-zinc-400 uppercase tracking-wider mb-2">
                        Topics
                      </h4>
                      <div className="flex flex-wrap gap-2">
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
                  <Eye size={16} />
                </div>
                <h3 className="font-medium text-zinc-200">Visual Analysis</h3>
                {getStatusIndicator(video.visual_analysis_status)}
              </div>

              {video.visual_analysis_status === 'processing' && (
                <div className="flex items-center gap-3 p-4 bg-zinc-900 border border-zinc-800 rounded-lg">
                  <Loader2 size={18} className="animate-spin text-purple-400" />
                  <span className="text-sm text-zinc-400">Analyzing visual content...</span>
                </div>
              )}

              {(!video.visual_analysis_status || video.visual_analysis_status === 'pending') && (
                <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-lg">
                  <p className="text-sm text-zinc-500 mb-3">
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
                      <Eye size={14} />
                    )}
                    Trigger Visual Analysis
                  </button>
                  {visualAnalysisError && (
                    <p className="mt-2 text-xs text-red-400 flex items-center gap-1">
                      <AlertCircle size={12} />
                      {visualAnalysisError}
                    </p>
                  )}
                </div>
              )}

              {video.visual_analysis_status === 'failed' && (
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
                      <Eye size={14} />
                    )}
                    Retry
                  </button>
                </div>
              )}

              {video.visual_analysis_status === 'completed' && video.ai_analyze_text && (
                <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-lg">
                  <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
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
