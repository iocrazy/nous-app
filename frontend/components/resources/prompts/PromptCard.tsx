// frontend/components/resources/prompts/PromptCard.tsx
//
// Text first, picture beside (spec §3.3). The positive is the object; the
// thumbnail is evidence of what it produced.
import React from 'react';
import { useTranslation } from 'react-i18next';
import { FormTag, OriginTag } from '../../prompts/PromptTags';
import { PromptThumbs } from '../../prompts/PromptThumbs';
import { langAvailability, paramChips, promptText, type PromptEntry, type PromptLang, type PromptSlide } from '../../../services/promptsService';

export interface PromptCardProps {
  entry: PromptEntry;
  lang: PromptLang;
  onSend: (entry: PromptEntry, slide?: PromptSlide) => void;
  onSaveAsTemplate: (entry: PromptEntry, slideNames?: string[]) => void;
  onOpen: (entry: PromptEntry) => void;
}

export function LangNote({ entry }: { entry: { positive_en: string | null; positive_zh: string | null } }): React.ReactElement | null {
  const { t } = useTranslation();
  const a = langAvailability(entry);
  if (a === 'none') return null;
  const text = a === 'both' ? 'EN · 中' : a === 'en' ? t('prompts.shelf.enOnly', 'EN only') : t('prompts.shelf.zhOnly', '中 only');
  return <span className="text-[10.5px] text-content-3">{text}</span>;
}

export function PromptCard({ entry, lang, onSend, onSaveAsTemplate, onOpen }: PromptCardProps): React.ReactElement {
  const { t } = useTranslation();
  const text = promptText(entry, lang);
  const chips = entry.origin === 'captioned' ? [] : paramChips(entry.params);
  const muted = entry.origin === 'captioned';
  return (
    <div data-testid="prompt-card" data-origin={entry.origin ?? ''} data-form={entry.form}
      className="grid grid-cols-[1fr_64px] gap-x-3 gap-y-1.5 rounded-xl border border-line bg-card p-3">
      <div className="col-span-2 flex items-center gap-1.5">
        <span className="truncate text-[12.5px] font-semibold text-content">{entry.title}</span>
        <span className="flex-1" />
        <FormTag form={entry.form} />
        <OriginTag origin={entry.origin} params={entry.params} />
      </div>
      <pre className={`m-0 line-clamp-3 whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed ${muted ? 'text-content-3' : 'text-content'}`}>{text.positive}</pre>
      <PromptThumbs thumbs={entry.thumbs} size={64} />
      {text.negative && <pre className="col-span-2 m-0 line-clamp-1 whitespace-pre-wrap font-mono text-[10.5px] text-content-3">− {text.negative}</pre>}
      <div className="col-span-2 flex flex-wrap items-center gap-1.5">
        {chips.length > 0 && (
          <div data-testid="prompt-params" className="flex flex-wrap gap-1">
            {chips.map((c) => <span key={c} className="rounded-md border border-line px-1.5 font-mono text-[10.5px] text-content">{c}</span>)}
          </div>
        )}
        {muted && <span className="text-[10.5px] text-content-3">{t('prompts.shelf.captionedNote', 'No parameters — this text describes the picture, it did not make it')}</span>}
        <span className="flex-1" />
        <LangNote entry={entry} />
      </div>
      <div className="col-span-2 flex items-center gap-1.5 border-t border-dashed border-line pt-2">
        <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" onClick={() => onSend(entry)} disabled={!text.positive}>{t('prompts.shelf.sendToCanvas', 'Send to canvas')}</button>
        {entry.form !== 'template' && (
          <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" onClick={() => onSaveAsTemplate(entry)}>{t('prompts.shelf.saveAsTemplate', 'Save as template')}</button>
        )}
        <button type="button" className="rounded-md px-2 py-0.5 text-[10.5px] text-content-3" onClick={() => onOpen(entry)}>{t('prompts.shelf.open', 'Open')}</button>
        <span className="flex-1" />
        <span className="whitespace-nowrap text-[10.5px] text-content-3">{entry.updated_at.slice(0, 10)}</span>
      </div>
    </div>
  );
}
