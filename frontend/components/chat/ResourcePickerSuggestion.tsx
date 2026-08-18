import React from 'react';
import { useTranslation } from 'react-i18next';
import { FileText, Image, Video, Music, LayoutGrid } from 'lucide-react';
import type { ResourceSearchResult, ResourceSearchResponse } from '../../types';
import { resourceProcessingState } from './resourceStatus';
import { ResourceThumb } from './ResourceThumb';

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

interface Props {
  items: ResourceSearchResult[];
  query: string;
  loading: boolean;
  counts: ResourceSearchResponse['counts'];
  activeKind: '' | 'video' | 'image' | 'doc' | 'audio' | 'pdf';
  onKindChange: (kind: Props['activeKind']) => void;
  onSelect: (item: ResourceSearchResult) => void;
  activeIndex?: number;
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
}: Props): React.ReactElement {
  const { t } = useTranslation();

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
              activeKind === tab.key
                ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                : 'text-ink-400 hover:text-ink-200'
            }`}
          >
            <tab.Icon size={11} />
            {tab.label} <span className="opacity-60">{tab.count}</span>
          </button>
        ))}
      </div>

      {items.length === 0 ? (
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
        <span>{items.length > 0 && `${items.length} of ${counts.all}`}</span>
        <span>{t('chat.mentionPicker.hintKbd')}</span>
      </div>
    </div>
  );
}
