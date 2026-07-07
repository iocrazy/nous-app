// Inline detail pane for the Hotspots tab (spec §2 screen-2 right pane).
// Content mirrors the legacy HotspotInfoPanel but rendered in-flow (no portal),
// since the tab wants a fixed right column, not a floating island.
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, Flame, Plus } from 'lucide-react';
import { getHotspot, type Hotspot } from '../../services/topicService';

interface Props {
  hotspot: Hotspot | null;
  onSaveAsNote: (h: Hotspot) => void;
  onParse: (h: Hotspot) => void;
  onNotInterested: (h: Hotspot) => void;
}

export const HotspotDetail: React.FC<Props> = ({ hotspot, onSaveAsNote, onParse, onNotInterested }) => {
  const { t } = useTranslation();
  const [full, setFull] = useState<Hotspot | null>(null);

  useEffect(() => {
    if (!hotspot) {
      setFull(null);
      return;
    }
    setFull(hotspot);
    let alive = true;
    getHotspot(hotspot.id)
      .then((h) => alive && setFull(h))
      .catch((err) => console.error('hotspot detail load failed', err));
    return () => {
      alive = false;
    };
  }, [hotspot]);

  if (!hotspot) {
    return (
      <div className="flex h-full items-center justify-center rounded-xl bg-island px-4 py-10 text-center text-sm text-content-3">
        {t('inspiration.selectHotspot', 'Select a hotspot to see details')}
      </div>
    );
  }

  const h = full ?? hotspot;
  const summary = h.ai_summary || h.summary || '';
  const url = h.origin_url || h.url || undefined;

  return (
    <div className="flex h-full flex-col rounded-xl bg-island">
      <div className="flex-1 overflow-y-auto p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          {h.source_label && (
            <span className="rounded bg-island-2 px-1.5 py-0.5 text-[10px] font-bold text-content-2">{h.source_label}</span>
          )}
          {h.category && (
            <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[11px] text-indigo-300">#{h.category}</span>
          )}
        </div>
        <h4 className="mt-2 text-[15px] font-semibold leading-snug text-content">{h.title}</h4>
        <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-content-3 tabular-nums">
          {typeof h.heat === 'number' && (
            <span className="inline-flex items-center gap-1 text-amber-400">
              <Flame size={11} /> {h.heat}
            </span>
          )}
          {typeof h.source_count === 'number' && h.source_count > 1 && (
            <span>{t('inspiration.sourceCount', '{{count}} sources', { count: h.source_count })}</span>
          )}
        </div>
        {summary && (
          <>
            <div className="mt-4 text-[10.5px] font-semibold uppercase tracking-wider text-content-4">
              {t('inspiration.summary', 'Summary')}
            </div>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-content-2">{summary}</p>
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-line p-3">
        <button
          onClick={() => onSaveAsNote(h)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white"
        >
          <Plus size={13} /> {t('inspiration.saveAsNote', 'Save as note')}
        </button>
        <button onClick={() => onParse(h)} className="rounded-lg bg-island-2 px-3 py-1.5 text-xs text-content-2">
          {t('inspiration.parse', 'Parse')}
        </button>
        <button onClick={() => onNotInterested(h)} className="rounded-lg bg-island-2 px-3 py-1.5 text-xs text-content-2">
          {t('inspiration.notInterested', 'Not interested')}
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
