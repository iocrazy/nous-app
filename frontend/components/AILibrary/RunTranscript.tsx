import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight, Terminal, User, Bot, Wrench, AlertCircle } from 'lucide-react';
import type { AgentRunEvent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';

// Paperclip-style Transcript section for the Runs detail pane (P3, mig 285).
// "Nice" mode groups events into readable blocks (messages, folded tool-call
// cards, errors); "Raw" mode is a monospace two-column dump. Polls
// incrementally (after_seq) while the run is live.

const LIVE_POLL_MS = 5_000;

const typeIcon = (t: AgentRunEvent['event_type']) => {
  switch (t) {
    case 'user': return <User size={12} className="text-indigo-400" />;
    case 'assistant': return <Bot size={12} className="text-emerald-400" />;
    case 'tool_call': return <Wrench size={12} className="text-amber-400" />;
    case 'error': return <AlertCircle size={12} className="text-red-400" />;
    default: return <Terminal size={12} className="text-ink-500" />;
  }
};

const NiceEvent: React.FC<{ ev: AgentRunEvent }> = ({ ev }) => {
  const p = ev.payload || {};
  if (ev.event_type === 'tool_call') {
    const tool = String(p.tool ?? 'tool');
    const args = p.args;
    const skillHint =
      args && typeof args === 'object' && 'skill' in (args as Record<string, unknown>)
        ? String((args as Record<string, unknown>).skill)
        : typeof args === 'string' && args.includes('"skill"')
          ? ''
          : '';
    return (
      <details className="group rounded-md border border-ink-800 bg-ink-950/60">
        <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs text-ink-300">
          <ChevronRight size={12} className="text-ink-600 transition-transform group-open:rotate-90" />
          {typeIcon(ev.event_type)}
          <span className="font-mono font-medium">{tool}</span>
          {skillHint && <span className="font-mono text-ink-500">({skillHint})</span>}
          <span className="ml-auto text-[10px] text-ink-600">#{ev.seq}</span>
        </summary>
        <div className="space-y-2 border-t border-ink-800/70 px-3 py-2">
          {p.args != null && (
            <div>
              <div className="mb-0.5 text-[10px] uppercase tracking-wide text-ink-600">args</div>
              <pre className="whitespace-pre-wrap break-words rounded bg-ink-900/80 p-2 text-[11px] text-ink-300">
                {typeof p.args === 'string' ? p.args : JSON.stringify(p.args, null, 2)}
              </pre>
            </div>
          )}
          {p.result != null && (
            <div>
              <div className="mb-0.5 text-[10px] uppercase tracking-wide text-ink-600">result</div>
              <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded bg-ink-900/80 p-2 text-[11px] text-ink-300 custom-scrollbar">
                {typeof p.result === 'string' ? p.result : JSON.stringify(p.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      </details>
    );
  }

  const content = String(p.content ?? p.message ?? JSON.stringify(p));
  const tint =
    ev.event_type === 'error'
      ? 'border-red-500/30 bg-red-500/5 text-red-200'
      : ev.event_type === 'user'
        ? 'border-ink-800 bg-ink-900/60 text-ink-300'
        : 'border-ink-800 bg-ink-950/60 text-ink-200';
  return (
    <div className={`rounded-md border px-3 py-2 ${tint}`}>
      <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-ink-500">
        {typeIcon(ev.event_type)}
        {ev.event_type}
        <span className="ml-auto text-ink-600">#{ev.seq}</span>
      </div>
      <pre className="whitespace-pre-wrap break-words text-xs leading-relaxed">{content}</pre>
    </div>
  );
};

export const RunTranscript: React.FC<{ runId: string; isRunning: boolean }> = ({
  runId, isRunning,
}) => {
  const { t } = useTranslation();
  const [events, setEvents] = useState<AgentRunEvent[]>([]);
  const [mode, setMode] = useState<'nice' | 'raw'>('nice');
  const [loaded, setLoaded] = useState(false);
  const lastSeqRef = useRef(0);

  const fetchEvents = useCallback(async () => {
    try {
      const resp = await aiLibraryService.getRunEvents(runId, lastSeqRef.current);
      if (resp.items.length > 0) {
        lastSeqRef.current = resp.items[resp.items.length - 1].seq;
        setEvents((prev) => [...prev, ...resp.items]);
      }
    } catch (err) {
      console.error('[RunTranscript] getRunEvents failed:', err);
    } finally {
      setLoaded(true);
    }
  }, [runId]);

  // Reset + initial fetch per run.
  useEffect(() => {
    lastSeqRef.current = 0;
    setEvents([]);
    setLoaded(false);
    void fetchEvents();
  }, [fetchEvents]);

  // Incremental poll while live.
  useEffect(() => {
    if (!isRunning) return;
    const id = window.setInterval(() => void fetchEvents(), LIVE_POLL_MS);
    return () => window.clearInterval(id);
  }, [isRunning, fetchEvents]);

  if (loaded && events.length === 0) return null; // pre-mig-285 runs have no events

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900/60 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-500">
          {t('aiLibrary.agents.runs.transcript', 'Transcript')} ({events.length})
        </h4>
        <div className="inline-flex overflow-hidden rounded-md border border-ink-700">
          {(['nice', 'raw'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`px-2.5 py-1 text-[10px] font-medium capitalize transition-colors ${
                mode === m
                  ? 'bg-ink-700 text-ink-100'
                  : 'bg-ink-800/60 text-ink-400 hover:text-ink-200'
              }`}
            >
              {m === 'nice'
                ? t('aiLibrary.agents.runs.transcriptNice', 'Nice')
                : t('aiLibrary.agents.runs.transcriptRaw', 'Raw')}
            </button>
          ))}
        </div>
      </div>

      {mode === 'nice' ? (
        <div className="space-y-2">
          {events.map((ev) => (
            <NiceEvent key={ev.seq} ev={ev} />
          ))}
        </div>
      ) : (
        <div className="overflow-hidden rounded-md border border-ink-800">
          {events.map((ev) => (
            <div
              key={ev.seq}
              className="grid grid-cols-[90px_1fr] gap-2 border-b border-ink-800/60 px-2 py-1.5 font-mono text-[11px] last:border-b-0"
            >
              <span className="text-ink-500">
                #{ev.seq} {ev.event_type}
              </span>
              <span className="break-all text-ink-300">
                {JSON.stringify(ev.payload)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
};

export default RunTranscript;
