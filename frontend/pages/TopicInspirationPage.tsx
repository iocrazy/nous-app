// frontend/pages/TopicInspirationPage.tsx
import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb } from 'lucide-react';
import { islandUI } from '../utils/featureFlags';
import { getHotspots, getHotspotDates, type Hotspot } from '../services/topicService';
import { useToast } from '../components/Toast';
import { Timeline } from '../components/TopicInspiration/Timeline';
import { HotspotInfoPanel } from '../components/TopicInspiration/HotspotInfoPanel';
import { FloatingParse } from '../components/TopicInspiration/FloatingParse';
import { CurrentHotspots } from '../components/TopicInspiration/CurrentHotspots';
import { SourceHealthBadge } from '../components/TopicInspiration/SourceHealthBadge';

const CATEGORIES = ['all', 'model', 'product', 'industry', 'paper', 'tips'] as const;
const TOP_HOTSPOTS_COUNT = 5;

export const TopicInspirationPage: React.FC = () => {
  const { t } = useTranslation();
  const island = islandUI();
  const { addToast } = useToast();
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [dates, setDates] = useState<string[]>([]);
  const [day, setDay] = useState<string | undefined>(undefined);
  const [category, setCategory] = useState<string>('all');
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Hotspot | null>(null);

  // addToast is wrapped in useCallback in Toast context and is referentially stable,
  // but omitted here per brief guidance to prevent any potential infinite-refetch risk.
  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const [hs, ds] = await Promise.all([getHotspots(day, category), getHotspotDates()]);
        if (!alive) return;
        setHotspots(hs);
        setDates(ds);
      } catch (err) {
        if (alive) addToast(`Failed to load topics: ${(err as Error).message}`, 'error');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [day, category]); // eslint-disable-line react-hooks/exhaustive-deps

  // Top N hotspots by score for the CurrentHotspots block
  const topByScore = useMemo<Hotspot[]>(
    () =>
      hotspots
        .filter((h) => typeof h.score === 'number' && h.score !== null)
        .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
        .slice(0, TOP_HOTSPOTS_COUNT),
    [hotspots],
  );

  const cPrimary = island ? 'text-content' : 'text-ink-50';
  const cSub = island ? 'text-content-3' : 'text-ink-400';

  function cycleDay() {
    if (dates.length === 0) return;
    if (!day) {
      setDay(dates[0]);
    } else {
      const idx = dates.indexOf(day);
      setDay(idx < 0 || idx >= dates.length - 1 ? undefined : dates[idx + 1]);
    }
  }

  return (
    <div className="max-w-[1180px] mx-auto px-6 py-6">
      {/* Page header */}
      <div className="flex items-center gap-2">
        <Lightbulb size={22} className={island ? 'text-content-2' : 'text-ink-300'} />
        <h1 className={`text-[22px] font-bold ${cPrimary}`}>{t('topic.title')}</h1>
        <div className="ml-auto">
          <SourceHealthBadge />
        </div>
      </div>
      <p className={`text-xs mt-1 ${cSub}`}>{t('topic.subtitle')}</p>

      {/* Controls row: category chips + date button */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        {CATEGORIES.map((cat) => {
          const active = category === cat;
          return (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-colors capitalize ${
                active
                  ? island
                    ? 'bg-accent text-white'
                    : 'bg-indigo-600 text-white'
                  : island
                  ? 'bg-island-2 text-content-3 hover:text-content'
                  : 'bg-ink-800 text-ink-400 hover:text-ink-200'
              }`}
            >
              {cat}
            </button>
          );
        })}

        <button
          onClick={cycleDay}
          className={`ml-auto px-3 py-1 rounded-full text-xs font-medium transition-colors ${
            island
              ? 'bg-island-2 border border-line-strong text-content-2 hover:text-content'
              : 'bg-ink-800 border border-ink-700 text-ink-300 hover:text-ink-100'
          }`}
        >
          {day || 'Today'}
        </button>
      </div>

      {/* Status / loading */}
      <div className={`mt-2 text-xs ${cSub}`}>
        {loading ? t('common.loading') : `${hotspots.length} hotspots`}
      </div>

      {/* Current Hotspots block — rendered above timeline when scored hotspots exist */}
      {!loading && topByScore.length > 0 && (
        <div className="mt-4">
          <CurrentHotspots items={topByScore} />
        </div>
      )}

      {/* Timeline — selectedId wired for highlight ring */}
      <div className="mt-4">
        {!loading && hotspots.length > 0 && (
          <Timeline
            hotspots={hotspots}
            onSelect={setSelected}
            selectedId={selected?.id}
          />
        )}
        {!loading && hotspots.length === 0 && (
          <p className={`text-sm ${cSub}`}>No hotspots found.</p>
        )}
      </div>

      {/* Right info island portal */}
      <HotspotInfoPanel hotspot={selected} />

      {/* Floating parse widget — bottom-right, 3 states */}
      <FloatingParse />
    </div>
  );
};
