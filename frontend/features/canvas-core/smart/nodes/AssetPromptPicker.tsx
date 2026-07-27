/**
 * Prompt Library picker for the canvas — browse resources that carry a
 * generation prompt, search/filter them, and pick one to seed a Prompt
 * node (spec 2026-07-26-asset-prompt-management, Phase 2 Task 2).
 *
 * Search is debounced 300ms to avoid a query per keystroke. Trigger-tag
 * chips come from fetchAllTags() filtered to `prompt_trigger === true`
 * (Settings → Tags → Prompt Trigger Tags) — the same tags that make an
 * asset carry a Prompt panel in the first place.
 *
 * EN/中 toggle controls both which prompt field is previewed per row and
 * which one `onPick` reports as the chosen lang; the caller decides how to
 * apply it (e.g. writing gen_prompt vs gen_prompt_zh into a Prompt node).
 *
 * Styling reuses the canvas popover convention from CanvasMentionPicker /
 * ResourcePickerSuggestion (ink-900/ink-700/ink-800 dark palette), wrapped
 * in a centered modal (à la ResourcePicker) since this needs room for
 * search + chips + a scrolling list, not just an inline dropdown.
 */

import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Search, X } from 'lucide-react';

import { fetchPromptAssets, getResourceCoverUrl, type PromptAsset } from '../../../../services/resourceService';
import { fetchAllTags } from '../../../../services/unifiedTagService';
import type { Tag } from '../../../../types';

const DEBOUNCE_MS = 300;

interface Props {
  onPick: (asset: PromptAsset, lang: 'en' | 'zh') => void;
  onClose: () => void;
}

function firstLine(text: string | null): string {
  if (!text) return '';
  const nl = text.indexOf('\n');
  return nl === -1 ? text : text.slice(0, nl);
}

export function AssetPromptPicker({ onPick, onClose }: Props): React.ReactElement {
  const { t } = useTranslation();

  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [triggerTags, setTriggerTags] = useState<Tag[]>([]);
  const [activeTagId, setActiveTagId] = useState<string | null>(null);
  const [lang, setLang] = useState<'en' | 'zh'>('en');
  const [assets, setAssets] = useState<PromptAsset[]>([]);
  const [loading, setLoading] = useState(false);

  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function handleQueryChange(value: string): void {
    setQuery(value);
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => setDebouncedQuery(value), DEBOUNCE_MS);
  }

  // Trigger-tag chips — loaded once; fetchAllTags has its own short TTL cache.
  useEffect(() => {
    fetchAllTags()
      .then((tags) => setTriggerTags(tags.filter((tg) => tg.prompt_trigger)))
      .catch((err) => console.error('[AssetPromptPicker] fetchAllTags failed:', err));
  }, []);

  // Re-query whenever the debounced search or active tag filter changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchPromptAssets({
      query: debouncedQuery.trim() || undefined,
      tagId: activeTagId ?? undefined,
    })
      .then((rows) => {
        if (!cancelled) setAssets(rows);
      })
      .catch((err) => {
        if (!cancelled) console.error('[AssetPromptPicker] fetchPromptAssets failed:', err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery, activeTagId]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent): void {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  function handleBackdropClick(e: React.MouseEvent<HTMLDivElement>): void {
    if (e.target === e.currentTarget) onClose();
  }

  // Portal to <body>: this component renders inside an RF node subtree whose
  // ancestors carry CSS transforms — a transform makes the ancestor the
  // containing block for position:fixed, collapsing "fullscreen" to a small
  // box inside the node.
  return createPortal(
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-[2px] grid place-items-center nodrag nopan nowheel"
      onClick={handleBackdropClick}
      data-testid="asset-prompt-picker-backdrop"
    >
      <div
        className="bg-ink-900 border border-ink-700 rounded-lg shadow-xl w-[420px] max-h-[70vh] flex flex-col"
        data-testid="asset-prompt-picker"
      >
        <div className="flex items-center justify-between px-3 py-2 border-b border-ink-800 shrink-0">
          <h3 className="text-[13px] font-semibold text-ink-100">
            {t('canvas.assetPromptPicker.title', 'Prompt Library')}
          </h3>
          <div className="flex items-center gap-2">
            <div className="flex rounded-full border border-ink-700 overflow-hidden text-[10px]">
              <button
                type="button"
                onClick={() => setLang('en')}
                className={`px-2 py-0.5 ${
                  lang === 'en'
                    ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                    : 'text-ink-400 hover:text-ink-200'
                }`}
              >
                EN
              </button>
              <button
                type="button"
                onClick={() => setLang('zh')}
                className={`px-2 py-0.5 ${
                  lang === 'zh'
                    ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                    : 'text-ink-400 hover:text-ink-200'
                }`}
              >
                中
              </button>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="w-5 h-5 grid place-items-center rounded text-ink-400 hover:text-ink-200 hover:bg-ink-800"
            >
              <X size={12} />
            </button>
          </div>
        </div>

        <div className="px-3 pt-2 pb-1.5 shrink-0">
          <div className="relative">
            <Search
              size={12}
              className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-500 pointer-events-none"
            />
            <input
              type="text"
              value={query}
              onChange={(e) => handleQueryChange(e.target.value)}
              placeholder={t('canvas.assetPromptPicker.searchPlaceholder', 'Search by filename…')}
              className="w-full bg-ink-800 border border-ink-700 rounded text-[12px] text-ink-100 pl-6 pr-2 py-1 outline-none focus:border-[var(--accent-border)] placeholder:text-ink-500"
            />
          </div>
        </div>

        {triggerTags.length > 0 && (
          <div className="flex flex-wrap gap-1 px-3 pb-1.5 shrink-0">
            <button
              type="button"
              onClick={() => setActiveTagId(null)}
              className={`text-[10px] px-2 py-0.5 rounded-full border ${
                activeTagId === null
                  ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] border-[var(--accent-border)]'
                  : 'text-ink-400 border-ink-700 hover:text-ink-200'
              }`}
            >
              {t('canvas.assetPromptPicker.all', 'All')}
            </button>
            {triggerTags.map((tg) => (
              <button
                key={String(tg.id)}
                type="button"
                onClick={() => setActiveTagId(String(tg.id))}
                className={`text-[10px] px-2 py-0.5 rounded-full border ${
                  activeTagId === String(tg.id)
                    ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] border-[var(--accent-border)]'
                    : 'text-ink-400 border-ink-700 hover:text-ink-200'
                }`}
              >
                {tg.name}
              </button>
            ))}
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-1.5 pb-1.5 min-h-0">
          {assets.length === 0 ? (
            <div className="px-3 py-6 text-center text-[12px] text-ink-500">
              {loading ? '…' : t('canvas.assetPromptPicker.noResults', 'No prompt assets found')}
            </div>
          ) : (
            assets.map((asset) => {
              const promptText = lang === 'en' ? asset.gen_prompt : asset.gen_prompt_zh;
              const hasNegative = lang === 'en' ? !!asset.gen_prompt_negative : !!asset.gen_prompt_negative_zh;
              return (
                <button
                  key={asset.id}
                  type="button"
                  onClick={() => onPick(asset, lang)}
                  className="w-full text-left flex items-center gap-2 px-1.5 py-1.5 rounded hover:bg-ink-800/50"
                  data-testid="asset-prompt-picker-row"
                >
                  <span className="w-9 h-9 shrink-0 rounded overflow-hidden bg-ink-800">
                    <img
                      src={getResourceCoverUrl(asset.id)}
                      alt=""
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        (e.currentTarget as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  </span>
                  <span className="flex-1 min-w-0">
                    <span className="flex items-center gap-1">
                      <span className="block text-[12px] text-ink-100 truncate">
                        {firstLine(promptText) || asset.filename}
                      </span>
                      {hasNegative && <span className="shrink-0 text-[9px] text-red-400">−neg</span>}
                    </span>
                    <span className="block text-[10px] text-ink-500 truncate">{asset.filename}</span>
                  </span>
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
