// features/canvas-core/library/LibraryPromptList.tsx
//
// The left column of the Prompts page (spec §3.4): one row per PromptEntry.
// Not a LibraryGrid — a prompt is read, not glanced at, so the row is text
// with a 36px thumbnail, and there is no multi-select (insertion is one at a
// time by design; merging slides is the user's job).
import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { FormTag } from '../../../components/prompts/PromptTags';
import { PromptThumbs } from '../../../components/prompts/PromptThumbs';
import { promptText, type PromptEntry, type PromptLang } from '../../../services/promptsService';

export interface LibraryPromptListProps {
  items: PromptEntry[];
  activeKey: string | null;
  onActivate: (key: string) => void;
  lang: PromptLang;
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
  emptyLabel: string;
}

export function LibraryPromptList({ items, activeKey, onActivate, lang, loading, error, onRetry, emptyLabel }: LibraryPromptListProps): React.ReactElement {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    rootRef.current?.querySelector('[data-active="true"]')?.scrollIntoView?.({ block: 'nearest' });
  }, [activeKey]);

  if (error) {
    return (
      <div className="flex flex-col items-start gap-1 p-3 text-[11px] text-danger" data-testid="library-prompt-error">
        {t('canvas.library.promptsLoadFailed', 'Could not load prompts')}
        <button type="button" className="nodrag rounded border border-canvas-line px-2 py-0.5 text-canvas-text" onClick={onRetry}>{t('canvas.library.retry', 'Retry')}</button>
      </div>
    );
  }
  if (loading && items.length === 0) {
    return <div className="flex items-center gap-2 p-3 text-[11px] text-canvas-muted"><Loader2 size={12} className="animate-spin" />{t('canvas.library.loading', 'Loading…')}</div>;
  }
  if (items.length === 0) {
    return <p className="p-3 text-[11px] text-canvas-muted" data-testid="library-prompt-empty">{emptyLabel}</p>;
  }
  return (
    <div ref={rootRef} className="flex flex-col gap-px overflow-y-auto p-1" role="listbox" aria-label={t('canvas.library.promptsList', 'Prompts')}>
      {items.map((entry) => {
        const active = entry.key === activeKey;
        const muted = entry.origin === 'captioned';
        const first = promptText(entry, lang).positive;
        const withText = entry.slides?.filter((s) => promptText(s, lang).positive).length ?? 0;
        return (
          <div
            key={entry.key}
            role="option"
            aria-selected={active}
            data-testid="library-prompt-row"
            data-key={entry.key}
            data-active={active ? 'true' : 'false'}
            onClick={() => onActivate(entry.key)}
            className={`nodrag grid cursor-pointer grid-cols-[36px_1fr_auto] items-center gap-x-2 rounded-lg px-1.5 py-1 ${active ? 'bg-[var(--accent-soft)] ring-1 ring-inset ring-[var(--accent-border)]' : 'hover:bg-canvas-card'} ${muted ? 'text-canvas-muted' : 'text-canvas-text'}`}
          >
            <PromptThumbs thumbs={entry.thumbs} count={entry.form === 'album' ? entry.slides?.length : undefined} size={36} />
            <span className="truncate text-[12px] font-medium">{entry.title}</span>
            <FormTag form={entry.form} />
            <span className="col-start-2 col-end-4 truncate text-[10.5px] text-canvas-muted">
              {entry.form === 'album'
                ? t('canvas.library.slidesWithText', { count: withText, total: entry.slides?.length ?? 0, defaultValue: '{{count}} of {{total}} slides with text' })
                : first}
            </span>
          </div>
        );
      })}
    </div>
  );
}
