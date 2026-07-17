// frontend/components/Inspiration/HotspotsWorkspace.tsx
// Hotspots tab main area: ranked list (left) + inline detail (right).
// hotspots/loading/applyState are owned by the page (single useHotspots
// instance, task-3); this component is a pure props consumer so hiding a
// hotspot or filtering by category stays in sync with the Notes-tab side
// panel and category chips, which read off the same data.
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flame, List, Clock } from 'lucide-react';
import { HotspotDetail } from './HotspotDetail';
import { HotspotTimeline } from './HotspotTimeline';
import { topHotspots } from '../TopicInspiration/hotspotRanking';
import type { Hotspot, HotspotStatePatch } from '../../services/topicService';

type HotspotsView = 'ranked' | 'timeline';
const VIEW_KEY = 'inspiration.hotspotsView';

interface Props {
  hotspots: Hotspot[];
  loading: boolean;
  applyState: (h: Hotspot, patch: HotspotStatePatch) => Promise<void>;
  activeCategory: string | null;
  onSaveAsNote: (h: Hotspot) => void;
  onParse: (h: Hotspot) => void;
}

export const HotspotsWorkspace: React.FC<Props> = ({
  hotspots,
  loading,
  applyState,
  activeCategory,
  onSaveAsNote,
  onParse,
}) => {
  const { t } = useTranslation();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<HotspotsView>(
    () => (localStorage.getItem(VIEW_KEY) === 'timeline' ? 'timeline' : 'ranked'),
  );

  const setViewPersist = (v: HotspotsView) => {
    setView(v);
    localStorage.setItem(VIEW_KEY, v);
  };

  // Shared filter (hidden + category) applied above the view split so both the
  // ranked list and the chronological timeline show the same set.
  const visible = useMemo(
    () => hotspots.filter((h) => !h.is_hidden && (!activeCategory || h.category === activeCategory)),
    [hotspots, activeCategory],
  );

  const ranked = useMemo(() => topHotspots(visible, 50), [visible]);
  // Opening the Hotspots tab should auto-select the top-ranked hotspot so the
  // detail pane shows content immediately (mockup v7 UX).
  const selected = ranked.find((h) => h.id === selectedId) ?? ranked[0] ?? null;

  const notInterested = async (h: Hotspot) => {
    try {
      await applyState(h, { is_hidden: true });
    } catch (err) {
      console.error('hide hotspot failed', err);
    }
  };

  const toggleBtn = (v: HotspotsView, label: string, Icon: typeof List) => (
    <button
      onClick={() => setViewPersist(v)}
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-semibold ${
        view === v ? 'bg-island text-indigo-300' : 'text-content-4'
      }`}
    >
      <Icon size={11} /> {label}
    </button>
  );

  const toolbar = (
    <div className="mb-2.5 flex items-center">
      <div className="ml-auto flex rounded-md bg-island-2 p-0.5">
        {toggleBtn('ranked', t('inspiration.viewRanked', 'Ranked'), List)}
        {toggleBtn('timeline', t('inspiration.viewTimeline', 'Timeline'), Clock)}
      </div>
    </div>
  );

  const empty = !loading && visible.length === 0;

  return (
    <div>
      {toolbar}
      {empty ? (
        <div className="rounded-xl bg-island px-4 py-10 text-center text-sm text-content-3">
          {t('inspiration.noHotspots', 'No hotspots for this day.')}
        </div>
      ) : view === 'timeline' ? (
        <HotspotTimeline
          hotspots={visible}
          onSaveAsNote={onSaveAsNote}
          onParse={onParse}
          applyState={applyState}
        />
      ) : (
        <div className="flex gap-2.5" style={{ minHeight: 420 }}>
          <div className="w-[260px] shrink-0 space-y-1.5 overflow-y-auto rounded-xl bg-island p-2 xl:w-[340px] 2xl:w-[400px]">
            {ranked.map((h) => (
              <button
                key={h.id}
                onClick={() => setSelectedId(h.id)}
                className={`block w-full rounded-lg px-3 py-2 text-left ${
                  selected?.id === h.id ? 'bg-indigo-500/15' : 'hover:bg-island-2'
                }`}
              >
                <div className={`text-[12.5px] leading-snug ${selected?.id === h.id ? 'text-indigo-300' : 'text-content'}`}>
                  {h.title}
                </div>
                <div className="mt-1 flex items-center gap-2 text-[10px] text-content-4 tabular-nums">
                  {h.source_label && <span>{h.source_label}</span>}
                  {typeof h.heat === 'number' && (
                    <span className="inline-flex items-center gap-0.5">
                      <Flame size={9} /> {h.heat}
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
          <div className="min-w-0 flex-1">
            <HotspotDetail hotspot={selected} onSaveAsNote={onSaveAsNote} onParse={onParse} onNotInterested={notInterested} />
          </div>
        </div>
      )}
    </div>
  );
};
