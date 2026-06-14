import React, { useState, useCallback } from 'react';
import { Sparkles, Copy, FileText, PenTool, Wand2, Check, Loader2 } from 'lucide-react';
import type { Video } from '../types';
import {
  triggerTranscription, getTranscript,
  triggerSummary, getSummary,
  triggerVisualAnalysis,
} from '../services/aiService';

interface AudioToolsMenuProps {
  video: Video;
  /** Persist AI status / generated text back to the media row (handleUpdate). */
  onUpdate?: (id: string, updates: Partial<Video>) => void;
  /** Resource notes — editable inline when a setter is provided. */
  resourceNotes?: string;
  onNotesChange?: (notes: string) => void;
  onNotesBlur?: () => void;
  addToast: (message: string, type?: 'success' | 'error' | 'info') => void;
}

/**
 * Audio stage-head "tools" dropdown — the home for the AI actions
 * (Copy / Transcript / Summary / Analyze) and Notes editing that the cover-side
 * MediaCard used to host. The island audio Overview (AudioOverviewSide) mirrors
 * the clean mock `.side` stack, so these capabilities move here to stay reachable
 * without crowding the cover (D12: no capability removed). The action keys +
 * service calls + toasts are kept identical to MediaCard's handleAction.
 */
export const AudioToolsMenu: React.FC<AudioToolsMenuProps> = ({
  video,
  onUpdate,
  resourceNotes,
  onNotesChange,
  onNotesBlur,
  addToast,
}) => {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  const saveAIContent = useCallback((field: string, content: string) => {
    if (onUpdate && video.platform_id) {
      onUpdate(video.platform_id, {
        [field]: content,
        ai_generated_at: new Date().toISOString(),
      } as Partial<Video>);
    }
  }, [onUpdate, video.platform_id]);

  const handleAction = useCallback(async (action: string) => {
    const platformId = video.platform_id;

    if (action === 'copy') {
      const textToCopy = video.description || video.title || '';
      if (textToCopy) {
        navigator.clipboard.writeText(textToCopy).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        });
      }
      return;
    }

    if (!platformId) return;

    if (action === 'extract') {
      setLoadingAction('extract');
      try {
        if (video.transcript_status === 'completed') {
          const result = await getTranscript(platformId);
          saveAIContent('ai_extract_text', result.text || '');
          addToast('Transcription loaded', 'success');
        } else {
          await triggerTranscription(platformId);
          if (onUpdate) onUpdate(platformId, { transcript_status: 'processing' });
          addToast('Transcription started. Check back shortly.', 'info');
        }
      } catch (err: any) {
        addToast(err?.message || 'Transcription failed', 'error');
      } finally {
        setLoadingAction(null);
      }
    } else if (action === 'rewrite') {
      setLoadingAction('rewrite');
      try {
        if (video.summary_status === 'completed') {
          const result = await getSummary(platformId);
          const text = result.summary + (result.key_points?.length ? '\n\nKey Points:\n' + result.key_points.map(p => `- ${p}`).join('\n') : '');
          saveAIContent('ai_rewrite_text', text);
          addToast('Summary loaded', 'success');
        } else {
          await triggerSummary(platformId);
          if (onUpdate) onUpdate(platformId, { summary_status: 'processing' });
          addToast('Summary started. Check back shortly.', 'info');
        }
      } catch (err: any) {
        addToast(err?.message || 'Summary failed', 'error');
      } finally {
        setLoadingAction(null);
      }
    } else if (action === 'analyze') {
      setLoadingAction('analyze');
      try {
        await triggerVisualAnalysis(platformId);
        addToast('Visual analysis started', 'info');
      } catch (err: any) {
        const msg = err?.message || 'Visual analysis failed';
        if (msg.includes('501') || msg.includes('Not Implemented')) {
          addToast('Visual analysis is not yet available', 'info');
        } else {
          addToast(msg, 'error');
        }
      } finally {
        setLoadingAction(null);
      }
    }
  }, [video, onUpdate, saveAIContent, addToast]);

  const itemClass = 'flex items-center gap-2 w-full text-left px-3 py-2 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors disabled:opacity-60';

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        title="Track tools"
        aria-label="Track tools"
        className="p-1.5 text-ink-400 hover:text-ink-200 hover:bg-ink-800 rounded-lg transition-colors"
      >
        <Sparkles size={16} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 z-20 w-56 bg-ink-900 border border-ink-700 rounded-lg shadow-xl py-1">
            <button className={itemClass} onClick={() => handleAction('copy')}>
              {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
              {copied ? 'Copied' : 'Copy text'}
            </button>
            <button className={itemClass} disabled={loadingAction === 'extract'} onClick={() => handleAction('extract')}>
              {loadingAction === 'extract' ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} className="text-teal-300" />}
              Transcript
            </button>
            <button className={itemClass} disabled={loadingAction === 'rewrite'} onClick={() => handleAction('rewrite')}>
              {loadingAction === 'rewrite' ? <Loader2 size={14} className="animate-spin" /> : <PenTool size={14} className="text-violet-300" />}
              Summary
            </button>
            <button className={itemClass} disabled={loadingAction === 'analyze'} onClick={() => handleAction('analyze')}>
              {loadingAction === 'analyze' ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} className="text-indigo-300" />}
              Analyze
            </button>
            {onNotesChange && (
              <div className="border-t border-ink-700 mt-1 pt-2 px-3 pb-2">
                <label className="block text-[10px] font-semibold text-ink-500 uppercase tracking-wider mb-1">Notes</label>
                <textarea
                  value={resourceNotes || ''}
                  onChange={(e) => onNotesChange(e.target.value)}
                  onBlur={onNotesBlur}
                  placeholder="Add notes..."
                  rows={2}
                  className="w-full bg-ink-800/50 border border-ink-700/50 rounded-lg px-2 py-1.5 text-xs text-ink-300 placeholder-ink-600 focus:outline-none focus:border-indigo-500/50 resize-none"
                />
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
};
