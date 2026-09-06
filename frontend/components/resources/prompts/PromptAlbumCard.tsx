// frontend/components/resources/prompts/PromptAlbumCard.tsx
//
// One card that opens (spec §3.3): collapsed = stacked thumbs + count; open =
// one row per slide with its own Send. Textless slides stay visible and
// disabled so "5 of 6 have text" is a number the eye can check.
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FormTag, OriginTag } from '../../prompts/PromptTags';
import { PromptThumbs } from '../../prompts/PromptThumbs';
import { promptText, type PromptEntry, type PromptLang, type PromptSlide } from '../../../services/promptsService';
import { usePromptThumbSrc } from '../../prompts/usePromptThumbSrc';
import type { PromptCardProps } from './PromptCard';

export function PromptAlbumCard({ entry, lang, onSend, onSaveAsTemplate, onOpen }: PromptCardProps): React.ReactElement {
  const src = usePromptThumbSrc();
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // Memoised, not a bare `?? []`: a fresh literal each render would make the
  // `withText` memo below recompute every time and defeat its own purpose.
  const slides = useMemo(() => entry.slides ?? [], [entry.slides]);
  const withText = useMemo(() => slides.filter((s) => promptText(s, lang).positive), [slides, lang]);
  return (
    <div data-testid="prompt-card" data-origin={entry.origin ?? ''} data-form="album" className="col-span-full rounded-xl border border-line bg-card p-3">
      <div className="flex items-center gap-1.5">
        {!open && <PromptThumbs thumbs={entry.thumbs} count={slides.length} size={40} />}
        <span className="truncate text-[12.5px] font-semibold text-content">{entry.title}</span>
        <span className="text-[10.5px] text-content-3">· {t('prompts.shelf.slidesWithText', { count: withText.length, total: slides.length, defaultValue: '{{count}} of {{total}} slides have text' })}</span>
        <span className="flex-1" />
        <FormTag form="album" />
        <OriginTag origin={entry.origin} params={entry.params} />
        <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? t('prompts.shelf.collapse', 'Collapse') : t('prompts.shelf.expand', 'Expand')}
        </button>
      </div>
      {open && (
        <div className="mt-2 grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-1.5">
          {slides.map((s) => {
            const text = promptText(s, lang);
            const has = !!text.positive;
            return (
              <div key={s.name} data-testid="prompt-slide-row" className={`grid grid-cols-[40px_1fr_auto] items-start gap-2 rounded-lg bg-island-2 p-1.5 ${has ? '' : 'opacity-60'}`}>
                {s.url ? <img src={src(s.url)} alt="" className="h-10 w-10 rounded-md object-cover" /> : <span className="block h-10 w-10 rounded-md bg-card" />}
                <div className="min-w-0">
                  <pre className={`m-0 line-clamp-2 whitespace-pre-wrap font-mono text-[10.5px] ${has ? 'text-content' : 'text-content-3'}`}>{has ? text.positive : t('prompts.shelf.noPromptOnSlide', 'No prompt on this slide')}</pre>
                  <span className="text-[9.5px] text-content-3">{s.name}{s.positive_en && s.positive_zh ? ' · EN · 中' : s.positive_zh ? ' · 中' : s.positive_en ? ' · EN' : ''}</span>
                </div>
                <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" disabled={!has} onClick={() => onSend(entry, s)}>{t('prompts.shelf.send', 'Send')}</button>
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-2 flex items-center gap-1.5 border-t border-dashed border-line pt-2">
        {/* No "send all" button (ruling R10): the canvas insert channel carries
            ONE node per navigation, so a bulk control could only ever deliver
            the last slide. Per-slide Send above covers the need honestly. */}
        <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" onClick={() => onSaveAsTemplate(entry, withText.map((s) => s.name))}>{t('prompts.shelf.saveAsTemplateEllipsis', 'Save as template…')}</button>
        <button type="button" className="rounded-md px-2 py-0.5 text-[10.5px] text-content-3" onClick={() => onOpen(entry)}>{t('prompts.shelf.openAlbum', 'Open album')}</button>
        <span className="flex-1" />
        <span className="whitespace-nowrap text-[10.5px] text-content-3">{entry.updated_at.slice(0, 10)}</span>
      </div>
    </div>
  );
}

export type { PromptSlide };
