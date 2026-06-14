import React, { useState, useCallback } from 'react';
import { Copy, FileText, PenTool, Wand2, Check, Loader2 } from 'lucide-react';
import type { Video } from '../types';
import {
  triggerTranscription, getTranscript,
  triggerSummary, getSummary,
  triggerVisualAnalysis,
} from '../services/aiService';

interface AudioToolsMenuItemsProps {
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
 * Audio AI actions (Copy / Transcript / Summary / Analyze) + Notes editing,
 * rendered as bare menu items so they can be FOLDED into the shared stage-head
 * More ("…") menu for the island audio stage only (the mock stage-head is just
 * back · title · Share / Download / … — no extra icon). These capabilities used
 * to live in the cover-side MediaCard, which AudioOverviewSide replaced with the
 * clean mock `.side` stack, so they move here to stay reachable (D12: nothing
 * removed). The action keys + service calls + toasts are identical to MediaCard's
 * handleAction. Item styling matches the surrounding More-menu items.
 */
export const AudioToolsMenuItems: React.FC<AudioToolsMenuItemsProps> = ({
  video,
  onUpdate,
  resourceNotes,
  onNotesChange,
  onNotesBlur,
  addToast,
}) => {
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

  // Matches the surrounding shared More-menu item styling so the folded section
  // is visually seamless.
  const itemClass = 'flex items-center gap-2 w-full text-left px-3 py-1.5 text-xs text-ink-400 hover:bg-ink-800 hover:text-ink-200 transition-colors disabled:opacity-60';

  return (
    <>
      <button className={itemClass} onClick={() => handleAction('copy')}>
        {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
        {copied ? 'Copied' : 'Copy text'}
      </button>
      <button className={itemClass} disabled={loadingAction === 'extract'} onClick={() => handleAction('extract')}>
        {loadingAction === 'extract' ? <Loader2 size={13} className="animate-spin" /> : <FileText size={13} className="text-teal-300" />}
        Transcript
      </button>
      <button className={itemClass} disabled={loadingAction === 'rewrite'} onClick={() => handleAction('rewrite')}>
        {loadingAction === 'rewrite' ? <Loader2 size={13} className="animate-spin" /> : <PenTool size={13} className="text-violet-300" />}
        Summary
      </button>
      <button className={itemClass} disabled={loadingAction === 'analyze'} onClick={() => handleAction('analyze')}>
        {loadingAction === 'analyze' ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} className="text-indigo-300" />}
        Analyze
      </button>
      {onNotesChange && (
        <div className="px-3 pt-1.5 pb-2">
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
    </>
  );
};
