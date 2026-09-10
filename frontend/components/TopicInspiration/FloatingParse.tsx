// frontend/components/TopicInspiration/FloatingParse.tsx
import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Eye, FileText, Link2, Loader2, Mic, X } from 'lucide-react';
import { useToast } from '../Toast';
import { detectParseMode } from './parseModeDetect';
import {
  parseShareLink,
  parseBatchLinks,
  getSodaPlaylist,
  downloadSodaTracks,
  type SodaPlaylistResult,
} from '../../services/parserService';
import { fetchAllTags, createTag } from '../../services/unifiedTagService';
import { EagleTagPicker } from '../EagleTagPicker';
import type { Tag } from '../../types';

type Phase = 'collapsed' | 'input' | 'result';

const URL_RE = /https?:\/\/[^\s]+/g;

interface ParseOutcome {
  kind: 'single' | 'batch' | 'playlist';
  title: string;
  detail: string;
  playlist?: SodaPlaylistResult;
}

type IntentKey = 'transcribe' | 'summarize' | 'analyze';
const AI_INTENTS: ReadonlyArray<{ key: IntentKey; label: string; Icon: typeof Mic }> = [
  { key: 'transcribe', label: 'Transcript', Icon: Mic },
  { key: 'summarize', label: 'Summary', Icon: FileText },
  { key: 'analyze', label: 'Analyze', Icon: Eye },
];
const NO_INTENTS: Record<IntentKey, boolean> = { transcribe: false, summarize: false, analyze: false };

export const FloatingParse: React.FC<{
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  initialUrl?: string;
}> = ({ open, onOpenChange, initialUrl }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [phase, setPhase] = useState<Phase>('collapsed');
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ParseOutcome | null>(null);

  // Controlled mode: when a parent passes `open`, keep phase in sync with it.
  // Untouched (early-return) when `open` is undefined, so the uncontrolled
  // callers (e.g. TopicInspirationPage's bare `<FloatingParse />`) are
  // byte-for-byte unaffected.
  useEffect(() => {
    if (open === undefined) return;
    if (open) {
      setPhase((p) => (p === 'collapsed' ? 'input' : p));
      if (initialUrl) setInput((prev) => (prev ? prev : initialUrl));
    } else {
      setPhase('collapsed');
    }
  }, [open, initialUrl]);

  // Tag state
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([]);
  const [intents, setIntents] = useState<Record<IntentKey, boolean>>(NO_INTENTS);
  // Pipeline 组的三枚系统标签由上面的意图按钮承载，不再当普通标签给用户勾。
  const pickerTags = useMemo(() => allTags.filter((tg) => tg.group_name !== 'Pipeline'), [allTags]);

  const detection = useMemo(() => detectParseMode(input), [input]);

  // Load tags once on mount
  useEffect(() => {
    fetchAllTags()
      .then(setAllTags)
      .catch((err) => {
        console.error('FloatingParse: failed to load tags', err);
      });
  }, []);

  // Unified parse: route by the detected mode so playlist / batch links don't
  // get mis-handled as a single link (which fails with "Track unavailable").
  const onAnalyze = async () => {
    const text = input.trim();
    const urls = text.match(URL_RE) || [];
    setBusy(true);
    try {
      if (detection.mode === 'playlist') {
        const pl = await getSodaPlaylist(text);
        setResult({
          kind: 'playlist',
          title: pl.title || t('topic.playlist', 'Playlist'),
          detail: t('topic.tracksFound', '{{n}} tracks', { n: pl.tracks.length }),
          playlist: pl,
        });
      } else if (detection.mode === 'batch') {
        const res = await parseBatchLinks(urls, {
          video_bool: true,
          cover_bool: true,
          tag_ids: selectedTagIds,
          ...intents,
        });
        setResult({
          kind: 'batch',
          title: t('topic.batchParse', 'Batch'),
          detail: t('topic.batchSubmitted', '{{n}} dispatched, {{f}} failed', {
            n: res.submitted,
            f: res.failed,
          }),
        });
      } else {
        const res = (await parseShareLink(text, {
          video_bool: true,
          cover_bool: true,
          tag_ids: selectedTagIds,
          ...intents,
        })) as { title?: string; videos?: Array<{ title?: string }> };
        setResult({
          kind: 'single',
          title: res?.title || res?.videos?.[0]?.title || t('topic.parsed', 'Parsed'),
          detail: t('topic.downloadDispatched', 'Download dispatched — see Task Center'),
        });
      }
      setPhase('result');
    } catch (e) {
      addToast(`${(e as Error).message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const onDownloadPlaylist = async (onlyNew = false) => {
    if (!result?.playlist) return;
    // Skip tracks already in the user's library (resolve endpoint marked each
    // track `downloaded`). "Download all" stays as an escape hatch so a deleted
    // file can still be re-pulled (the download workflow re-checks the real
    // file on disk and re-downloads when it's gone).
    const source = onlyNew
      ? result.playlist.tracks.filter((tr) => !tr.downloaded)
      : result.playlist.tracks;
    if (source.length === 0) {
      addToast(t('topic.nothingNew', 'Nothing new to download'), 'info');
      return;
    }
    setBusy(true);
    try {
      const items = source.map((tr) => ({
        id: tr.track_id,
        kind: tr.kind ?? ('track' as const),
      }));
      const r = await downloadSodaTracks(items, result.title || undefined);
      addToast(
        t('topic.playlistDispatched', '{{n}} tracks dispatched', { n: r.submitted }),
        'success',
      );
      onReset();
    } catch (e) {
      addToast(`${(e as Error).message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const onClose = () => {
    setPhase('collapsed');
    setResult(null);
    onOpenChange?.(false);
  };

  const onReset = () => {
    setPhase('collapsed');
    setResult(null);
    setInput('');
    setSelectedTagIds([]);
    setIntents(NO_INTENTS);
    onOpenChange?.(false);
  };

  if (phase === 'collapsed') {
    // Controlled mode (parent passes `open`): the entry point lives in the
    // parent's own UI (e.g. InspirationPage's top-bar "Parse URL" button),
    // so don't also render the floating trigger pill — that would give the
    // page two redundant Parse entry points. Uncontrolled callers (`open`
    // undefined, e.g. legacy TopicInspirationPage's bare `<FloatingParse />`)
    // keep the floating button as their only entry point.
    if (open !== undefined) return null;
    return (
      <button
        onClick={() => {
          setPhase('input');
          onOpenChange?.(true);
        }}
        className={`fixed bottom-6 right-6 z-40 flex items-center gap-2 px-4 py-2.5 rounded-full shadow-lg text-sm font-semibold bg-island border border-line-strong text-content`}
      >
        <Link2 size={16} /> {t('topic.parseLink', 'Parse Link')}
      </button>
    );
  }

  return (
    <div
      className={`fixed bottom-6 right-6 z-40 w-[360px] rounded-[var(--r-lg)] shadow-2xl p-4 bg-island border border-line-strong`}
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

          {/* AI Processing + Tag Picker — hidden for playlist mode */}
          {detection.mode !== 'playlist' && (
            <>
              {/* AI Processing toggles */}
              <div className="mt-2">
                <div className="text-[11px] text-content-3 mb-1">AI Processing</div>
                <div className="grid grid-cols-3 gap-1.5">
                  {AI_INTENTS.map(({ key, label, Icon }) => {
                    const active = intents[key];
                    return (
                      <button
                        key={key}
                        type="button"
                        data-testid={`ai-intent-${key}`}
                        aria-label={`AI intent: ${label}`}
                        aria-pressed={active}
                        onClick={() => setIntents((prev) => ({ ...prev, [key]: !prev[key] }))}
                        className={`flex items-center justify-center gap-1 px-2 py-1.5 rounded-md text-[11px] font-medium border transition-colors ${
                          active
                            ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] border-[var(--accent-border)]'
                            : 'bg-island-2 text-content-2 border-line'
                        }`}
                      >
                        <Icon size={12} />
                        {label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Tag picker */}
              <div className="mt-2">
                <EagleTagPicker
                  selectedTagIds={selectedTagIds}
                  onTagsChange={setSelectedTagIds}
                  allTags={pickerTags}
                  onCreate={async (name, color) => {
                    try {
                      const tag = await createTag({ name, color, type: 'user' });
                      setAllTags((prev) => [...prev, tag]);
                      return tag as Tag;
                    } catch (err) {
                      console.error('FloatingParse: failed to create tag', err);
                      return null;
                    }
                  }}
                />
              </div>
            </>
          )}

          <button
            disabled={busy || detection.count === 0}
            onClick={onAnalyze}
            className="mt-2 w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold bg-[var(--accent-soft)] text-[var(--accent-text)] border border-[var(--accent-border)] disabled:opacity-50"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : null}
            {t('topic.analyze', 'Analyze')}
          </button>
        </>
      )}

      {phase === 'result' && result && (
        <div className="space-y-2">
          <div className="text-sm font-medium text-content">{result.title}</div>
          <div className="text-xs text-content-3">{result.detail}</div>
          {result.kind === 'playlist' ? (
            // Playlist: getSodaPlaylist only fetched the track list — the user
            // dispatches the actual download here. Already-owned tracks are
            // skipped via "Download new"; "Download all" re-pulls everything.
            (() => {
              const total = result.playlist?.tracks.length ?? 0;
              const owned =
                result.playlist?.downloaded_count ??
                result.playlist?.tracks.filter((tr) => tr.downloaded).length ??
                0;
              const fresh = result.playlist?.new_count ?? total - owned;
              return (
                <div className="space-y-1.5">
                  {owned > 0 && (
                    <div className="text-[11px] text-content-3">
                      {t('topic.playlistDedup', '{{total}} tracks · {{owned}} in library · {{fresh}} new', {
                        total,
                        owned,
                        fresh,
                      })}
                    </div>
                  )}
                  {fresh > 0 && (
                    <button
                      disabled={busy}
                      onClick={() => onDownloadPlaylist(true)}
                      className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold bg-amber-500/[0.12] text-[var(--amb-tx,#b45309)] border border-amber-500/35 disabled:opacity-50"
                    >
                      {busy ? <Loader2 size={14} className="animate-spin" /> : null}
                      {t('topic.downloadNew', 'Download new ({{n}})', { n: fresh })}
                    </button>
                  )}
                  <button
                    disabled={busy || !total}
                    onClick={() => onDownloadPlaylist(false)}
                    className={`w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 ${
                      fresh > 0
                        ? 'bg-island-2 text-content-2 border border-line'
                        : 'bg-amber-500/[0.12] text-[var(--amb-tx,#b45309)] border border-amber-500/35'
                    }`}
                  >
                    {busy && fresh === 0 ? <Loader2 size={14} className="animate-spin" /> : null}
                    {t('topic.downloadAll', 'Download all ({{n}})', { n: total })}
                  </button>
                </div>
              );
            })()
          ) : (
            // single/batch already dispatched the download at parse time.
            <button
              onClick={onReset}
              className="w-full px-3 py-2 rounded-lg text-sm font-semibold bg-island-2 text-content-2 border border-line"
            >
              {t('common.done', 'Done')}
            </button>
          )}
        </div>
      )}
    </div>
  );
};
