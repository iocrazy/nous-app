// frontend/pages/TopicInspirationPage.tsx
import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb, PauseCircle, Search, X } from 'lucide-react';
import {
  getHotspots,
  getHotspotDates,
  setHotspotState,
  getInterest,
  setInterest,
  type Hotspot,
  type HotspotView,
  type HotspotStatePatch,
} from '../services/topicService';
import { fetchAllTags } from '../services/unifiedTagService';
import type { Tag } from '../types';
import { useToast } from '../components/Toast';
import { Timeline } from '../components/TopicInspiration/Timeline';
import { HotspotInfoPanel } from '../components/TopicInspiration/HotspotInfoPanel';
import { FloatingParse } from '../components/TopicInspiration/FloatingParse';
import { CurrentHotspots } from '../components/TopicInspiration/CurrentHotspots';
import { SourceHealthBadge } from '../components/TopicInspiration/SourceHealthBadge';
import { TopicFilterBar } from '../components/TopicInspiration/TopicFilterBar';
import { topHotspots, partitionBySignal } from '../components/TopicInspiration/hotspotRanking';
import { useTopicModuleStatus } from '../hooks/useTopicModuleEnabled';
import { InspirationPage } from './InspirationPage';

const TOP_HOTSPOTS_COUNT = 5;

export const TopicInspirationPage: React.FC = () => {
  const notesEnabled = import.meta.env.VITE_FEATURE_INSPIRATION_NOTES === 'true';
  const { t } = useTranslation();
  const { addToast } = useToast();
  const { visible: moduleVisible, enabled: moduleEnabled } = useTopicModuleStatus();
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [dates, setDates] = useState<string[]>([]);
  const [day, setDay] = useState<string | undefined>(undefined);
  const [category, setCategory] = useState<string>('all');
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [tagFilterIds, setTagFilterIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Hotspot | null>(null);
  const [queryInput, setQueryInput] = useState('');
  const [query, setQuery] = useState('');
  const [view, setView] = useState<HotspotView>('all');
  const [refreshKey, setRefreshKey] = useState(0);
  const [showLowSignal, setShowLowSignal] = useState(false);
  const [interestInput, setInterestInput] = useState('');
  const [interestSaved, setInterestSaved] = useState<string | null>(null);
  const [interestSaving, setInterestSaving] = useState(false);
  const searching = query.trim().length > 0;

  // Load the saved interest once (drives the For You editor + empty state).
  useEffect(() => {
    // Flag-on renders InspirationPage instead (see the early-return below,
    // placed after all hooks per rules-of-hooks) — skip the fetch so the
    // legacy page doesn't fire a request nobody reads.
    if (notesEnabled) return;
    let alive = true;
    (async () => {
      try {
        const it = await getInterest();
        if (!alive) return;
        setInterestInput(it.interest_text);
        setInterestSaved(it.has_embedding ? it.interest_text : '');
      } catch (err) {
        console.error('load interest failed', err);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  async function saveInterest() {
    setInterestSaving(true);
    try {
      const it = await setInterest(interestInput.trim());
      setInterestSaved(it.has_embedding ? it.interest_text : '');
      if (!it.has_embedding && it.interest_text) {
        addToast(t('topic.interestNoEmbed', 'Saved, but embedding is unavailable'), 'info');
      } else {
        addToast(t('topic.interestSaved', 'Interests saved'), 'success');
      }
      if (view === 'foryou') setRefreshKey((k) => k + 1); // re-rank
    } catch (err) {
      addToast(`${(err as Error).message}`, 'error');
    } finally {
      setInterestSaving(false);
    }
  }

  // Debounce the search box so we hit the backend at most once per pause,
  // not on every keystroke.
  useEffect(() => {
    const id = setTimeout(() => setQuery(queryInput), 300);
    return () => clearTimeout(id);
  }, [queryInput]);

  // Load the curated tag pool once for the Tag filter chip.
  useEffect(() => {
    if (notesEnabled) return;
    let alive = true;
    (async () => {
      try {
        const tags = await fetchAllTags();
        if (alive) setAllTags(tags);
      } catch (err) {
        console.error('load tags for topic filter failed', err);
      }
    })();
    return () => {
      alive = false;
    };
  }, [notesEnabled]);

  // addToast is wrapped in useCallback in Toast context and is referentially stable,
  // but omitted here per brief guidance to prevent any potential infinite-refetch risk.
  useEffect(() => {
    // Same rationale as the getInterest effect above — flag-on skips
    // straight to InspirationPage, so getHotspots/getHotspotDates here would
    // be wasted requests (and could surface a stray "Failed to load topics"
    // toast for a page that's never shown).
    if (notesEnabled) return;
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const [hs, ds] = await Promise.all([
          getHotspots(day, category, query, view, selectedSources, tagFilterIds),
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
  }, [day, category, query, view, selectedSources, tagFilterIds, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // Current Hotspots = highest BLENDED rank (LLM relevance × objective heat),
  // not raw LLM score — so genuinely viral items surface over LLM guesses.
  const topByScore = useMemo<Hotspot[]>(
    () => topHotspots(hotspots, TOP_HOTSPOTS_COUNT),
    [hotspots],
  );

  // Low-signal noise reduction (browse view only): fold scored-but-low items.
  const { main: mainHotspots, low: lowHotspots } = useMemo(
    () =>
      view === 'all' && !searching
        ? partitionBySignal(hotspots)
        : { main: hotspots, low: [] as Hotspot[] },
    [hotspots, view, searching],
  );

  const cPrimary = 'text-content';
  const cSub = 'text-content-3';

  // Flag-gated switch to the new notes workspace (Task 7 InspirationPage).
  // Placed after all hook calls above so both branches keep hooks order
  // identical (rules-of-hooks) — this is a plain constant, not a hook.
  if (notesEnabled) return <InspirationPage />;

  // Display switch (admin) — when not visible, hide the whole page (also
  // blocks direct-URL access, not just the nav item). A paused pipeline
  // (enabled=false) does NOT hide the page — it shows a notice below instead.
  if (!moduleVisible) {
    return (
      <div className="max-w-[1180px] mx-auto px-6 py-24 text-center">
        <Lightbulb size={32} className={`mx-auto mb-3 text-content-4`} />
        <h1 className={`text-lg font-bold ${cPrimary}`}>{t('topic.title')}</h1>
        <p className={`mt-2 text-sm ${cSub}`}>
          {t('topic.moduleDisabled', 'This feature is currently turned off.')}
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-[1180px] mx-auto px-6 py-6">
      {/* Page header */}
      <div className="flex items-center gap-2">
        <Lightbulb size={22} className={'text-content-2'} />
        <h1 className={`text-[22px] font-bold ${cPrimary}`}>{t('topic.title')}</h1>
        <div className="ml-auto">
          <SourceHealthBadge />
        </div>
      </div>
      <p className={`text-xs mt-1 ${cSub}`}>{t('topic.subtitle')}</p>

      {/* Processing paused (admin switch) — the surface stays browsable,
          content just stops updating until the pipeline is re-enabled. */}
      {!moduleEnabled && (
        <div className="mt-3 flex items-center gap-1.5 text-xs text-content-3">
          <PauseCircle size={14} className="shrink-0" />
          <span>{t('topic.updatesPaused', 'Content updates are paused')}</span>
        </div>
      )}

      {/* Search box — server-side, debounced (only in the default "all" view) */}
      {view === 'all' && (
      <div className="mt-4 relative">
        <Search
          size={15}
          className={`absolute left-3 top-1/2 -translate-y-1/2 text-content-4`}
        />
        <input
          type="text"
          value={queryInput}
          onChange={(e) => setQueryInput(e.target.value)}
          placeholder={t('topic.searchPlaceholder', 'Search hotspots...')}
          className={`w-full rounded-lg pl-9 pr-9 py-2 text-sm outline-none transition-colors bg-island-2 border border-line-strong text-content placeholder:text-content-4 focus:border-accent/50`}
        />
        {queryInput && (
          <button
            onClick={() => setQueryInput('')}
            aria-label={t('common.clear', 'Clear')}
            className={`absolute right-2.5 top-1/2 -translate-y-1/2 p-0.5 rounded text-content-4 hover:text-content`}
          >
            <X size={15} />
          </button>
        )}
      </div>
      )}

      {/* Resources-style dropdown filter bar: View / Category / Source / Date */}
      <div className="mt-4">
        <TopicFilterBar
          view={view}
          onView={switchView}
          category={category}
          onCategory={setCategory}
          selectedSources={selectedSources}
          onSources={setSelectedSources}
          day={day}
          dates={dates}
          onDay={setDay}
          allTags={allTags}
          selectedTagIds={tagFilterIds}
          onTagIdsChange={setTagFilterIds}
          searching={searching}
        />
      </div>

      {/* Interest editor — For You view only */}
      {view === 'foryou' && (
        <div className="mt-3 flex flex-col sm:flex-row gap-2">
          <input
            type="text"
            value={interestInput}
            onChange={(e) => setInterestInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') saveInterest();
            }}
            placeholder={t('topic.interestPlaceholder', '关键词，空格分隔，如：综艺 明星 影视 世界杯')}
            className={`flex-1 rounded-lg px-3 py-2 text-sm outline-none transition-colors bg-island-2 border border-line-strong text-content placeholder:text-content-4 focus:border-accent/50`}
          />
          <button
            onClick={saveInterest}
            disabled={interestSaving}
            className={`px-4 py-2 rounded-lg text-sm font-semibold transition-colors disabled:opacity-40 bg-accent text-white`}
          >
            {interestSaving ? t('common.loading', 'Loading...') : t('topic.saveInterest', 'Save')}
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
          <CurrentHotspots items={topByScore} onSelect={handleSelect} />
        </div>
      )}

      {/* Timeline — selectedId wired for highlight ring */}
      <div className="mt-4">
        {!loading && hotspots.length > 0 && (
          <Timeline
            hotspots={mainHotspots}
            onSelect={handleSelect}
            selectedId={selected?.id}
            onToggleSave={(h) => applyState(h, { is_saved: !h.is_saved })}
            onToggleHide={(h) => applyState(h, { is_hidden: !h.is_hidden })}
            grouped={view !== 'featured' && view !== 'foryou'}
          />
        )}

        {/* Folded low-signal noise (scored-but-low). Browse view only. */}
        {!loading && lowHotspots.length > 0 && (
          <div className="mt-3">
            <button
              onClick={() => setShowLowSignal((v) => !v)}
              className={`text-xs font-medium transition-colors text-content-3 hover:text-content`}
            >
              {showLowSignal
                ? t('topic.hideLowSignal', 'Hide low-signal')
                : t('topic.showLowSignal', {
                    count: lowHotspots.length,
                    defaultValue: 'Show {{count}} low-signal items',
                  })}
            </button>
            {showLowSignal && (
              <div className="mt-3 opacity-70">
                <Timeline
                  hotspots={lowHotspots}
                  onSelect={handleSelect}
                  selectedId={selected?.id}
                  onToggleSave={(h) => applyState(h, { is_saved: !h.is_saved })}
                  onToggleHide={(h) => applyState(h, { is_hidden: !h.is_hidden })}
                />
              </div>
            )}
          </div>
        )}

        {!loading && hotspots.length === 0 && (
          <p className={`text-sm ${cSub}`}>
            {view === 'saved'
              ? t('topic.noSaved', 'No saved hotspots yet.')
              : view === 'hidden'
              ? t('topic.noHidden', 'Nothing hidden.')
              : view === 'foryou'
              ? interestSaved
                ? t('topic.noForYouYet', 'No matches yet — check back as more hotspots are analyzed.')
                : t('topic.setInterestPrompt', 'Describe your interests above to get a personalized feed.')
              : t('topic.noHotspots', 'No hotspots found.')}
          </p>
        )}
      </div>

      {/* Right info island portal */}
      <HotspotInfoPanel
        hotspot={selected}
        onToggleSave={(h) => applyState(h, { is_saved: !h.is_saved })}
        onToggleHide={(h) => applyState(h, { is_hidden: !h.is_hidden })}
        onClose={() => setSelected(null)}
      />

      {/* Floating parse widget — bottom-right, 3 states */}
      <FloatingParse />
    </div>
  );
};
