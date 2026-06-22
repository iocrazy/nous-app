// frontend/pages/TopicInspirationPage.tsx
import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb, Search, X } from 'lucide-react';
import { islandUI } from '../utils/featureFlags';
import {
  getHotspots,
  getHotspotDates,
  setHotspotState,
  type Hotspot,
  type HotspotView,
  type HotspotStatePatch,
} from '../services/topicService';
import { useToast } from '../components/Toast';
import { Timeline } from '../components/TopicInspiration/Timeline';
import { HotspotInfoPanel } from '../components/TopicInspiration/HotspotInfoPanel';
import { FloatingParse } from '../components/TopicInspiration/FloatingParse';
import { CurrentHotspots } from '../components/TopicInspiration/CurrentHotspots';
import { SourceHealthBadge } from '../components/TopicInspiration/SourceHealthBadge';

const CATEGORIES = ['all', 'model', 'product', 'industry', 'paper', 'tips'] as const;
const VIEWS: HotspotView[] = ['all', 'saved', 'hidden'];
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
  const [queryInput, setQueryInput] = useState('');
  const [query, setQuery] = useState('');
  const [view, setView] = useState<HotspotView>('all');
  const [refreshKey, setRefreshKey] = useState(0);
  const searching = query.trim().length > 0;

  // Debounce the search box so we hit the backend at most once per pause,
  // not on every keystroke.
  useEffect(() => {
    const id = setTimeout(() => setQuery(queryInput), 300);
    return () => clearTimeout(id);
  }, [queryInput]);

  // addToast is wrapped in useCallback in Toast context and is referentially stable,
  // but omitted here per brief guidance to prevent any potential infinite-refetch risk.
  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const [hs, ds] = await Promise.all([
          getHotspots(day, category, query, view),
          getHotspotDates(),
        ]);
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
  }, [day, category, query, view, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Optimistically apply a state patch, drop the card if it no longer belongs
  // to the current view, persist, and revert (via refetch) on failure.
  async function applyState(h: Hotspot, patch: HotspotStatePatch) {
    setHotspots((prev) =>
      prev.flatMap((item) => {
        if (item.id !== h.id) return [item];
        const next = { ...item, ...patch };
        if (view === 'all' && next.is_hidden) return [];
        if (view === 'saved' && next.is_saved === false) return [];
        if (view === 'hidden' && next.is_hidden === false) return [];
        return [next];
      }),
    );
    setSelected((s) => (s && s.id === h.id ? { ...s, ...patch } : s));
    try {
      await setHotspotState(h.id, patch);
    } catch (err) {
      addToast(`Failed to update: ${(err as Error).message}`, 'error');
      setRefreshKey((k) => k + 1); // revert to server truth
    }
  }

  function handleSelect(h: Hotspot) {
    setSelected(h);
    if (!h.is_read) applyState(h, { is_read: true });
  }

  function switchView(v: HotspotView) {
    setView(v);
    setQueryInput('');
    setQuery('');
  }

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

      {/* View selector: All / Saved / Hidden */}
      <div className="mt-4 flex items-center gap-1">
        {VIEWS.map((v) => {
          const active = view === v;
          return (
            <button
              key={v}
              onClick={() => switchView(v)}
              className={`px-3 py-1 rounded-full text-xs font-medium capitalize transition-colors ${
                active
                  ? island
                    ? 'bg-accent text-white'
                    : 'bg-indigo-600 text-white'
                  : island
                  ? 'bg-island-2 text-content-3 hover:text-content'
                  : 'bg-ink-800 text-ink-400 hover:text-ink-200'
              }`}
            >
              {t(`topic.view_${v}`, v)}
            </button>
          );
        })}
      </div>

      {/* Search box — server-side, debounced (only in the default "all" view) */}
      {view === 'all' && (
      <div className="mt-3 relative">
        <Search
          size={15}
          className={`absolute left-3 top-1/2 -translate-y-1/2 ${
            island ? 'text-content-4' : 'text-ink-500'
          }`}
        />
        <input
          type="text"
          value={queryInput}
          onChange={(e) => setQueryInput(e.target.value)}
          placeholder={t('topic.searchPlaceholder', 'Search hotspots...')}
          className={`w-full rounded-lg pl-9 pr-9 py-2 text-sm outline-none transition-colors ${
            island
              ? 'bg-island-2 border border-line-strong text-content placeholder:text-content-4 focus:border-accent/50'
              : 'bg-ink-900 border border-ink-700 text-ink-100 placeholder:text-ink-500 focus:border-indigo-500/50'
          }`}
        />
        {queryInput && (
          <button
            onClick={() => setQueryInput('')}
            aria-label={t('common.clear', 'Clear')}
            className={`absolute right-2.5 top-1/2 -translate-y-1/2 p-0.5 rounded ${
              island ? 'text-content-4 hover:text-content' : 'text-ink-500 hover:text-ink-200'
            }`}
          >
            <X size={15} />
          </button>
        )}
      </div>
      )}

      {/* Controls row: category chips + date button (browse view only) */}
      {view === 'all' && (
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
          disabled={searching}
          title={searching ? t('topic.dateDisabledWhenSearching', 'Searching all dates') : undefined}
          className={`ml-auto px-3 py-1 rounded-full text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
            island
              ? 'bg-island-2 border border-line-strong text-content-2 hover:text-content'
              : 'bg-ink-800 border border-ink-700 text-ink-300 hover:text-ink-100'
          }`}
        >
          {day || 'Today'}
        </button>
      </div>
      )}

      {/* Status / loading */}
      <div className={`mt-2 text-xs ${cSub}`}>
        {loading
          ? t('common.loading')
          : searching
          ? t('topic.searchResults', {
              count: hotspots.length,
              query: query.trim(),
              defaultValue: '{{count}} results for "{{query}}"',
            })
          : `${hotspots.length} hotspots`}
      </div>

      {/* Current Hotspots block — browse affordance; only in the all view */}
      {!loading && view === 'all' && !searching && topByScore.length > 0 && (
        <div className="mt-4">
          <CurrentHotspots items={topByScore} />
        </div>
      )}

      {/* Timeline — selectedId wired for highlight ring */}
      <div className="mt-4">
        {!loading && hotspots.length > 0 && (
          <Timeline
            hotspots={hotspots}
            onSelect={handleSelect}
            selectedId={selected?.id}
            onToggleSave={(h) => applyState(h, { is_saved: !h.is_saved })}
            onToggleHide={(h) => applyState(h, { is_hidden: !h.is_hidden })}
          />
        )}
        {!loading && hotspots.length === 0 && (
          <p className={`text-sm ${cSub}`}>
            {view === 'saved'
              ? t('topic.noSaved', 'No saved hotspots yet.')
              : view === 'hidden'
              ? t('topic.noHidden', 'Nothing hidden.')
              : t('topic.noHotspots', 'No hotspots found.')}
          </p>
        )}
      </div>

      {/* Right info island portal */}
      <HotspotInfoPanel hotspot={selected} />

      {/* Floating parse widget — bottom-right, 3 states */}
      <FloatingParse />
    </div>
  );
};
