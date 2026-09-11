import React from 'react';
import { useTranslation } from 'react-i18next';
import { FileText, Image, Video, Music, LayoutGrid, Shapes, FileOutput } from 'lucide-react';
import type { ResourceSearchResult, ResourceSearchResponse } from '../../types';
import { resourceProcessingState } from './resourceStatus';
import { ResourceThumb } from './ResourceThumb';
import {
  AssetGridPicker,
  type AssetGridPickerHandle,
  type AssetGridQuery,
  type AssetGridRow,
} from '../assets/AssetGridPicker';
import {
  OutputMentionList,
  type OutputMentionListHandle,
} from './OutputMentionList';
import type { OutputMentionRow } from './outputMentionRows';

function _formatSize(n: number | null): string {
  if (!n) return '';
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)}KB`;
  return `${Math.round(n / (1024 * 1024))}MB`;
}

function _relative(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const days = Math.floor(diffMs / 86400000);
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

/**
 * The Assets tab (P5).
 *
 * OPTIONAL as a whole: a host that has no asset shelf to offer simply leaves
 * it out, and the popover is the five-kind resource picker it was before P5.
 * Both current hosts (`AIChatPanel.tsx` and, since the v2 batch,
 * `Todolist/IssueReplyBox.tsx`) pass it, built by `useMentionAssetsTab` so the
 * two cannot drift; the ruling-H "not in this phase" carve-out for the issue
 * box is closed.
 *
 * The parent owns `active` for the same reason it owns `activeKind`: it is the
 * parent that routes the arrow keys, and a tab this component kept to itself
 * would leave the keyboard aimed at whichever list the parent guessed.
 */
export interface AssetsTabProps {
  active: boolean;
  onActivate: () => void;
  /**
   * Rows the grid is showing, or `null` while nobody has asked yet.
   *
   * The null is load-bearing: an unvisited tab reading "Assets 0" states that
   * the user's library is empty, which is a claim no request has been made to
   * support. The grid reports this through `onCountChange` when an ANSWER
   * arrives, rather than the parent deriving it — so the number is what came
   * back, not what was asked for.
   */
  count: number | null;
  onCountChange: (count: number) => void;
  onSelect: (row: AssetGridRow) => void;
  fetch: (params: AssetGridQuery, signal: AbortSignal) => Promise<AssetGridRow[]>;
  /** Handle for the parent's ↑↓/Enter routing. */
  pickerRef?: React.Ref<AssetGridPickerHandle>;
}

/**
 * The Outputs tab (harness 3a Task 6) — this issue's registered outputs.
 *
 * OPTIONAL, and more narrowly so than Assets: citations are ISSUE-SCOPED (the
 * resolver's whole check is "was this version produced on this issue"), so the
 * chat panel — which has no issue behind it — passes this prop not at all and
 * its composer refuses `output_ref` outright. The issue reply box is the only
 * host with a third tab.
 *
 * Owned by the parent for the same reason `active` is on the assets tab: the
 * parent routes the arrow keys, and a tab this component kept to itself would
 * leave the keyboard aimed at whichever body the parent guessed.
 */
export interface OutputsTabProps {
  active: boolean;
  onActivate: () => void;
  /** One row per citable VERSION, latest-first with the older ones folded —
   *  already built by `toMentionRows`, so the popover never re-derives which
   *  version is current. */
  rows: OutputMentionRow[];
  loading: boolean;
  /** One readable line, or null. An empty list and a failed read are
   *  different answers and the body says which. */
  error: string | null;
  onSelect: (row: OutputMentionRow) => void;
  /** Handle for the parent's ↑↓/Enter routing. */
  listRef?: React.Ref<OutputMentionListHandle>;
}

/** The dropdown asks for at most this many rows; the grid caps what it draws
 *  at `ASSET_GRID_LIMIT`. 24 is the router's own default. */
const ASSET_SEARCH_LIMIT = 24;

/**
 * Shorter than the canvas's 300ms because the query here IS the `@` text, so
 * every keystroke is a new search rather than an occasional one — and the
 * endpoint costs four round trips per call, which is what the abort in
 * `AssetGridPicker` is there for.
 */
const ASSET_DEBOUNCE_MS = 200;

/** The Assets tab's copy. Its own `chat.mentionPicker.assets*` keys rather
 *  than the canvas's `canvas.mention.*`: the two pickers sit in different
 *  products and a shared string would tie their wording together for no
 *  reason. */
const ASSET_LABELS = (t: (key: string, def?: string) => string) => ({
  allTypes: t('chat.mentionPicker.assetsAllTypes', 'All'),
  loading: t('chat.mentionPicker.assetsLoading', 'Loading…'),
  empty: t('chat.mentionPicker.assetsEmpty', 'No assets found'),
  error: t('chat.mentionPicker.assetsError', 'Could not load the asset library'),
  preview: t('chat.mentionPicker.assetsPreview', 'Preview'),
  previewGroup: t('chat.mentionPicker.assets', 'Assets'),
  libraryLabel: t('chat.mentionPicker.library', 'Library'),
  inLibraryOnly: t('chat.mentionPicker.inLibraryOnly', 'In Library Only'),
});

interface Props {
  items: ResourceSearchResult[];
  query: string;
  loading: boolean;
  counts: ResourceSearchResponse['counts'];
  activeKind: '' | 'video' | 'image' | 'doc' | 'audio' | 'pdf';
  onKindChange: (kind: Props['activeKind']) => void;
  onSelect: (item: ResourceSearchResult) => void;
  activeIndex?: number;
  /** Omit to render the resource tabs alone. */
  assets?: AssetsTabProps;
  /** Omit on every host without an issue behind it. */
  outputs?: OutputsTabProps;
}

export function ResourcePickerSuggestion({
  items,
  query,
  loading,
  counts,
  activeKind,
  onKindChange,
  onSelect,
  activeIndex = 0,
  assets,
  outputs,
}: Props): React.ReactElement {
  const { t } = useTranslation();
  const assetsActive = Boolean(assets?.active);
  const outputsActive = Boolean(outputs?.active);
  // Any tab that is not one of the five resource KINDS. Named once so the
  // three places that ask "is the resource list the body right now?" cannot
  // answer differently — the bug a third tab invites is exactly that: the tab
  // strip learns about it, one body condition does not, and two lists render
  // at once under one set of arrow keys.
  const otherTabActive = assetsActive || outputsActive;

  const tabs: {
    key: Props['activeKind'];
    label: string;
    count: number;
    Icon: React.ComponentType<{ size?: number }>;
  }[] = [
    { key: '', label: t('chat.mentionPicker.all'), count: counts.all, Icon: LayoutGrid },
    { key: 'video', label: t('chat.mentionPicker.video'), count: counts.video, Icon: Video },
    // Audio outnumbers images in the real library (293 vs 159, measured
    // 2026-08-18) and the backend has always accepted `kinds=audio` — the tab
    // was simply never drawn, so those rows were reachable only by scrolling
    // "All". PDF stays out for now: 0 rows today, and a sixth tab starts
    // wrapping the strip.
    { key: 'audio', label: t('chat.mentionPicker.audio'), count: counts.audio, Icon: Music },
    { key: 'image', label: t('chat.mentionPicker.image'), count: counts.image, Icon: Image },
    { key: 'doc', label: t('chat.mentionPicker.doc'), count: counts.doc, Icon: FileText },
  ];

  return (
    <div
      className="bg-ink-900 border border-ink-700 rounded-lg shadow-xl w-[340px] p-1.5"
      data-testid="resource-picker"
    >
      {/* `flex-wrap`, not nowrap: five tabs carrying real four-digit counts
          ("All 1421", "Video 950", …) measure well past the 340px popover,
          and a nowrap strip would push the last one outside the rounded box
          where it cannot be clicked. Wrapping to a second row costs 18px and
          keeps every tab reachable. */}
      <div
        className="flex flex-wrap gap-1 px-1 pb-1.5 border-b border-ink-800"
        data-testid="resource-picker-tabs"
      >
        {tabs.map((tab) => (
          <button
            key={tab.key || 'all'}
            data-kind={tab.key || 'all'}
            onClick={() => onKindChange(tab.key)}
            className={`text-[11px] px-2 py-0.5 rounded-full inline-flex items-center gap-1 ${
              !otherTabActive && activeKind === tab.key
                ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                : 'text-ink-400 hover:text-ink-200'
            }`}
          >
            <tab.Icon size={11} />
            {tab.label} <span className="opacity-60">{tab.count}</span>
          </button>
        ))}
        {/* Assets last, after Doc: the five before it slice ONE population —
            files in the library — by kind, and this one is a different
            population entirely (library entities: a character, a location, a
            prompt). Dropping it among the kinds would read as a sixth file
            type. */}
        {assets && (
          <button
            data-kind="assets"
            data-testid="resource-picker-tab-assets"
            aria-pressed={assetsActive}
            onClick={assets.onActivate}
            className={`text-[11px] px-2 py-0.5 rounded-full inline-flex items-center gap-1 ${
              assetsActive
                ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                : 'text-ink-400 hover:text-ink-200'
            }`}
          >
            <Shapes size={11} />
            {t('chat.mentionPicker.assets', 'Assets')}
            {assets.count !== null && (
              <span className="opacity-60">{assets.count}</span>
            )}
          </button>
        )}
        {/* Outputs last of all. A third population again — not files, not
            library entities, but the things THIS ISSUE's agents made — and the
            only one scoped to the page the composer is sitting on. No count
            badge: the number is the rows already loaded for this issue rather
            than an answer to the typed query, and a badge that did not move
            with the search would read as a stale claim. */}
        {outputs && (
          <button
            data-kind="outputs"
            data-testid="resource-picker-tab-outputs"
            aria-pressed={outputsActive}
            onClick={outputs.onActivate}
            className={`text-[11px] px-2 py-0.5 rounded-full inline-flex items-center gap-1 ${
              outputsActive
                ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                : 'text-ink-400 hover:text-ink-200'
            }`}
          >
            <FileOutput size={11} />
            {t('outputs.mentionTab', 'Outputs')}
          </button>
        )}
      </div>

      {assets && (
        // Rendered unconditionally and told whether its tab is showing:
        // inactive means it draws nothing and asks nothing, while keeping the
        // type chip the user picked. A hidden tab that kept polling would
        // spend four round trips per keystroke on a list nobody is reading.
        <AssetGridPicker
          ref={assets.pickerRef}
          active={assetsActive}
          query={query}
          labels={ASSET_LABELS(t)}
          fetch={assets.fetch}
          onPick={assets.onSelect}
          onCountChange={assets.onCountChange}
          searchBox={false}
          // The grid opens narrowed to library members, so the pill is the
          // only way back to a script-imported or migrated asset. Without it
          // the chat shelf would be a dead end: the asset the writer can see
          // in the library UI would simply not be mentionable, and nothing on
          // screen would say why.
          libraryToggle
          theme="chat"
          limit={ASSET_SEARCH_LIMIT}
          debounceMs={ASSET_DEBOUNCE_MS}
        />
      )}

      {outputs && (
        // Rendered unconditionally and told whether its tab is showing, for
        // the same reason the asset grid is: inactive means it draws nothing
        // while keeping the highlight the reader left it on.
        <OutputMentionList
          ref={outputs.listRef}
          active={outputsActive}
          rows={outputs.rows}
          loading={outputs.loading}
          error={outputs.error}
          onPick={outputs.onSelect}
        />
      )}

      {otherTabActive ? null : items.length === 0 ? (
        <div className="px-3 py-6 text-center text-[12px] text-ink-500">
          {loading ? '…' : t('chat.mentionPicker.noResults', { q: query })}
        </div>
      ) : (
        // The limit is 50 now, so the list MUST scroll — an unbounded popover
        // pushes the tail below the viewport where it cannot be reached.
        <div className="py-1 max-h-[288px] overflow-y-auto" data-testid="resource-picker-list">
          {items.map((item, idx) => {
            const active = idx === activeIndex;
            const status = resourceProcessingState({
              kind: item.kind,
              mime: item.mime,
              transcriptStatus: item.transcript_status,
              summaryStatus: item.summary_status,
            });
            return (
              <button
                key={item.id}
                onClick={() => onSelect(item)}
                className={`w-full text-left flex items-center gap-2 px-2.5 py-1.5 rounded ${
                  active ? 'bg-[var(--accent-soft)]' : 'hover:bg-ink-800/50'
                }`}
                data-testid="resource-picker-row"
              >
                <span className="relative w-7 h-7 shrink-0">
                  <ResourceThumb
                    thumbnailUrl={item.thumbnail_url}
                    kind={item.kind}
                    iconSize={14}
                    imgClassName="w-7 h-7 object-cover rounded bg-ink-800"
                    iconClassName="w-7 h-7 flex items-center justify-center bg-ink-800 rounded"
                    imgTestId="resource-picker-thumb"
                    iconTestId="resource-picker-icon"
                  />
                  {status && (
                    <span
                      data-testid="resource-picker-status"
                      data-status={status}
                      title={t(
                        status === 'processing'
                          ? 'chat.mentionPicker.statusProcessing'
                          : 'chat.mentionPicker.statusUnprocessed',
                      )}
                      className={`absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-warn border border-ink-900 ${
                        status === 'processing' ? 'animate-pulse' : ''
                      }`}
                    />
                  )}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block text-[12px] text-ink-100 truncate">
                    {item.name}
                    <span className="text-ink-500"> · {item.scope.type}</span>
                  </span>
                  <span className="block text-[10px] text-ink-500">
                    {_relative(item.updated_at)}
                    {_formatSize(item.size) && ' · ' + _formatSize(item.size)}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      <div className="px-2 py-1 text-[10px] text-ink-500 border-t border-ink-800 flex justify-between">
        <span data-testid="resource-picker-count">
          {outputsActive
            ? outputs !== undefined
              && outputs.rows.length > 0
              && t('outputs.mentionCount', {
                count: outputs.rows.length,
                defaultValue: `${outputs.rows.length} outputs`,
              })
            : assetsActive
            ? assets?.count !== null &&
              assets !== undefined &&
              t('chat.mentionPicker.assetsCount', {
                count: assets.count as number,
                defaultValue: `${assets.count} assets`,
              })
              : items.length > 0 && `${items.length} of ${counts.all}`}
        </span>
        {/* Assets tab ONLY. The hint promises `↑↓ navigate · ↵ insert`, and
            `AIChatPanel.handleMentionKey` takes the arrow keys over only when
            the Assets tab is active — on the five resource tabs the highlight
            has been pinned at 0 since launch and Enter falls through to send.
            Printing it there is a user-visible claim about behaviour that does
            not exist (final review M4). Arrow navigation for the resource
            tabs is the other way to make this true; it is not a ten-line
            change (parent-owned index, per-tab reset, Enter routing), so the
            lie goes rather than the feature getting half-built. */}
        {/* The two tabs that actually move a highlight with ↑↓ and insert on
            ↵. The five resource tabs still pin `activeIndex` at 0, so printing
            it there would promise behaviour that does not exist. */}
        {otherTabActive && <span>{t('chat.mentionPicker.hintKbd')}</span>}
      </div>
    </div>
  );
}
