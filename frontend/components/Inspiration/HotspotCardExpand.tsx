// frontend/components/Inspiration/HotspotCardExpand.tsx
// The in-card expansion for the Hotspots timeline. Rendered as `children` of
// HotspotCard so the card + expansion read as one unit (redesign 2026-07-16).
// Shows ONLY what the card above does not already: the full body (when it
// differs from the summary), a compact meta row, and the action buttons.
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, EyeOff, Flame, Link2, Plus } from 'lucide-react';
import { getHotspot, type Hotspot } from '../../services/topicService';

interface Props {
  hotspot: Hotspot;
  onSaveAsNote: (h: Hotspot) => void;
  onParse: (h: Hotspot) => void;
  onNotInterested: (h: Hotspot) => void;
}

export const HotspotCardExpand: React.FC<Props> = ({ hotspot, onSaveAsNote, onParse, onNotInterested }) => {
  const { t } = useTranslation();
  const [full, setFull] = useState<Hotspot | null>(null);
  const [loadError, setLoadError] = useState(false);
  // Bumped by Retry to re-run the lazy fetch.
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setLoadError(false);
    let alive = true;
    getHotspot(hotspot.id)
      .then((h) => alive && setFull(h))
      .catch((err) => {
        if (!alive) return;
        setLoadError(true);
        console.error('hotspot detail load failed', err);
      });
    return () => {
      alive = false;
    };
  }, [hotspot.id, reloadKey]);

  const h = full ?? hotspot;
  const url = h.origin_url || h.url || undefined;
  const summary = (h.ai_summary || h.summary || '').trim();
  const body = (h.content_translated || h.content_original || '').trim();
  // Only show the body when it adds something the card's summary does not.
  const showBody = body.length > 0 && body !== summary;

  return (
    <div className="mt-3 border-t border-line pt-3">
      {!full && !loadError ? (
        <div className="space-y-2">
          <div className="h-3 w-full animate-pulse rounded bg-island-2" />
          <div className="h-3 w-2/3 animate-pulse rounded bg-island-2" />
        </div>
      ) : (
        <>
          {showBody && (
            <p className="max-h-[320px] overflow-y-auto whitespace-pre-wrap text-[13px] leading-relaxed text-content-2">
              {body}
            </p>
          )}

          <div
            className={`flex flex-wrap items-center gap-3 text-[11px] text-content-3 tabular-nums ${
              showBody ? 'mt-3' : ''
            }`}
          >
            {typeof h.heat === 'number' && (
              <span className="inline-flex items-center gap-1 text-amber-400">
                <Flame size={11} /> {h.heat}
              </span>
            )}
            {typeof h.source_count === 'number' && h.source_count > 1 && (
              <span>{t('inspiration.sourceCount', '{{count}} sources', { count: h.source_count })}</span>
            )}
            {h.category && (
              <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-indigo-300">#{h.category}</span>
            )}
          </div>
        </>
      )}

      {loadError && (
        <div className="mt-3 flex items-center gap-2 text-[11px] text-red-400">
          <span>{t('inspiration.detailLoadFailed', 'Failed to load details')}</span>
          <button
            onClick={() => setReloadKey((k) => k + 1)}
            className="rounded bg-island-2 px-1.5 py-0.5 text-content-2 hover:text-content"
          >
            {t('inspiration.retry', 'Retry')}
          </button>
        </div>
      )}

      {/* Actions */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          onClick={() => onSaveAsNote(h)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-indigo-400 active:scale-[0.98]"
        >
          <Plus size={13} /> {t('inspiration.saveAsNote', 'Save as note')}
        </button>
        <button
          onClick={() => onParse(h)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-line-strong px-3.5 py-1.5 text-xs font-medium text-content-2 transition hover:border-accent/40 hover:text-content active:scale-[0.98]"
        >
          <Link2 size={13} /> {t('inspiration.parse', 'Parse')}
        </button>
        <button
          onClick={() => onNotInterested(h)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-line-strong px-3.5 py-1.5 text-xs font-medium text-content-2 transition hover:border-accent/40 hover:text-content active:scale-[0.98]"
        >
          <EyeOff size={13} /> {t('inspiration.notInterested', 'Not interested')}
        </button>
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-xs text-content-3 hover:text-content-2"
          >
            {t('inspiration.openSource', 'Open source')} <ExternalLink size={11} />
          </a>
        )}
      </div>
    </div>
  );
};
