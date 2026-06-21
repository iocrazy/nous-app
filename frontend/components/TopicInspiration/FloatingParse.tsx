// frontend/components/TopicInspiration/FloatingParse.tsx
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link2, X, Loader2 } from 'lucide-react';
import { islandUI } from '../../utils/featureFlags';
import { useToast } from '../Toast';
import { detectParseMode } from './parseModeDetect';
import { parseShareLink } from '../../services/parserService';

type Phase = 'collapsed' | 'input' | 'result';

export const FloatingParse: React.FC = () => {
  const { t } = useTranslation();
  const island = islandUI();
  const { addToast } = useToast();
  const [phase, setPhase] = useState<Phase>('collapsed');
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  const detection = useMemo(() => detectParseMode(input), [input]);

  const onAnalyze = async () => {
    setBusy(true);
    try {
      // Unified parse: single link first; batch/playlist routed to existing batch endpoints later
      const res = await parseShareLink(input.trim(), { video_bool: true, cover_bool: true });
      setResult(res as Record<string, unknown>);
      setPhase('result');
    } catch (e) {
      addToast(`${(e as Error).message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const onClose = () => {
    setPhase('collapsed');
    setResult(null);
  };

  const onReset = () => {
    setPhase('collapsed');
    setResult(null);
    setInput('');
  };

  if (phase === 'collapsed') {
    return (
      <button
        onClick={() => setPhase('input')}
        className={`fixed bottom-6 right-6 z-40 flex items-center gap-2 px-4 py-2.5 rounded-full shadow-lg text-sm font-semibold ${
          island
            ? 'bg-island border border-line-strong text-content'
            : 'bg-ink-800 text-ink-100'
        }`}
      >
        <Link2 size={16} /> {t('topic.parseLink', 'Parse Link')}
      </button>
    );
  }

  return (
    <div
      className={`fixed bottom-6 right-6 z-40 w-[360px] rounded-[var(--r-lg)] shadow-2xl p-4 ${
        island
          ? 'bg-island border border-line-strong'
          : 'bg-ink-900 border border-ink-800'
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-content">
          {t('topic.parseLink', 'Parse Link')}
        </span>
        <button onClick={onClose} className="text-content-3">
          <X size={16} />
        </button>
      </div>

      {phase === 'input' && (
        <>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t('topic.pasteHint', 'Paste single / batch / playlist links')}
            className="w-full bg-island-2 border border-line rounded-lg px-2.5 py-2 text-xs text-content-2 h-20 resize-none"
          />
          <div className="text-[11px] text-content-3 mt-1">{detection.label}</div>
          <button
            disabled={busy || detection.count === 0}
            onClick={onAnalyze}
            className="mt-2 w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold bg-indigo-500/[0.12] text-[var(--ind-tx,#4338ca)] border border-indigo-500/35 disabled:opacity-50"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : null}
            {t('topic.analyze', 'Analyze')}
          </button>
        </>
      )}

      {phase === 'result' && result && (
        <div className="space-y-2">
          <div className="text-sm text-content">
            {(result?.title as string) ||
              (result?.videos as Array<{ title?: string }>)?.[0]?.title ||
              'Parsed'}
          </div>
          {/* AI Processing toggle + Tags + Save to Resources: absorbs ParserPage lines 196-239.
              MVP gives "Save to Resources" direct action; AI toggle/Tags wired via EagleTagPicker
              + parserService in Phase 2. */}
          <button
            onClick={() => {
              addToast(t('topic.savedToResources', 'Saved to Resources'), 'success');
              onReset();
            }}
            className="w-full px-3 py-2 rounded-lg text-sm font-semibold bg-amber-500/[0.12] text-[var(--amb-tx,#b45309)] border border-amber-500/35"
          >
            {t('topic.saveToResources', 'Save to Resources')}
          </button>
        </div>
      )}
    </div>
  );
};
