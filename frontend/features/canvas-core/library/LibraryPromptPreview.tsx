// features/canvas-core/library/LibraryPromptPreview.tsx
//
// Right column (spec §3.4): what Apply all would write, labelled by what it
// is. An album previews as its slides; the selected slide's negative and
// params sit beneath. Actions are named after the thing they act on, and are
// handed the slide they act on rather than leaving the parent to look it up
// (ruling R17).
//
// The substituted-language note sits in the HEADER, not in the Positive block
// label: an album has no Positive block, so a note living there would go
// missing for exactly the entries whose per-slide text is most often
// one-sided (ruling R18). Header placement makes it one note for whichever
// source is active.
import React from 'react';
import { useTranslation } from 'react-i18next';
import { FormTag, OriginTag } from '../../../components/prompts/PromptTags';
import { langAvailability, paramChips, promptText, type PromptEntry, type PromptLang, type PromptSlide } from '../../../services/promptsService';
import { usePromptThumbSrc } from '../../../components/prompts/usePromptThumbSrc';

export interface LibraryPromptPreviewProps {
  entry: PromptEntry | null;
  lang: PromptLang;
  onLangChange: (l: PromptLang) => void;
  slideName: string | null;
  onSlideChange: (name: string) => void;
  canAct: boolean;
  actHint: string;
  /** The slide to act on is passed IN, and the argument is authoritative
   *  (`undefined` for a non-album entry). The parent must not read its own
   *  `slideName` state inside these handlers: the per-slide Insert changes
   *  the selection and acts in the same tick, and React has not committed
   *  the new `slideName` by then — a parent reading its own state would act
   *  on the PREVIOUSLY selected slide (ruling R17). */
  onInsert: (slideName?: string) => void;
  onApplyAll: (slideName?: string) => void;
  onSaveAsTemplate: () => void;
  /** Whether "Save as template…" may be pressed. SEPARATE from `canAct`,
   *  which is about the aimed NODE: promoting a picture to a template needs
   *  no target at all, it needs write access. Optional and true at rest so a
   *  caller that has no such gate keeps the button live. */
  canSaveAsTemplate?: boolean;
}

export function activeSlide(entry: PromptEntry | null, slideName: string | null): PromptSlide | null {
  const slides = entry?.slides;
  if (!slides || slides.length === 0) return null;
  if (slideName) return slides.find((s) => s.name === slideName) ?? null;
  return slides.find((s) => s.positive_en || s.positive_zh) ?? slides[0];
}

const BLOCK_LABEL = 'mb-0.5 flex items-baseline gap-1.5 text-[9.5px] font-semibold uppercase tracking-[0.08em] text-canvas-muted';
const BTN = 'nodrag rounded-lg border border-canvas-line px-2.5 py-1 text-[11px] font-medium text-canvas-text disabled:opacity-40';

export function LibraryPromptPreview(p: LibraryPromptPreviewProps): React.ReactElement {
  const thumb = usePromptThumbSrc();
  const { t } = useTranslation();
  const { entry, lang } = p;
  if (!entry) {
    return <div className="flex flex-1 items-center justify-center p-6 text-center text-[11px] text-canvas-muted">{t('canvas.library.promptPickOne', 'Pick a prompt to preview it')}</div>;
  }
  const slide = activeSlide(entry, p.slideName);
  const src = slide ?? entry;
  const text = promptText(src, lang);
  const avail = langAvailability(src);
  const chips = entry.origin === 'captioned' ? [] : paramChips(entry.params);
  const insertLabel = slide ? t('canvas.library.insertSlide', { name: slide.name, defaultValue: 'Insert slide {{name}}' }) : t('canvas.library.insertPositive', 'Insert positive');
  const applyLabel = slide ? t('canvas.library.applyAllFrom', { name: slide.name, defaultValue: 'Apply all from {{name}}' }) : t('canvas.library.applyAll', 'Apply all');
  const canInsert = p.canAct && !!text.positive;

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="library-prompt-preview">
      <div className="flex items-center gap-1.5 px-2.5 pt-2">
        <span className="truncate text-[13px] font-semibold text-canvas-text">{entry.title}</span>
        <FormTag form={entry.form} />
        <OriginTag origin={entry.origin} params={entry.params} />
        <span className="flex-1" />
        {text.shownLang && text.shownLang !== lang && (
          <span className="shrink-0 text-[9.5px] text-canvas-muted">{text.shownLang === 'en' ? t('canvas.library.enOnly', 'EN only') : t('canvas.library.zhOnly', '中 only')}</span>
        )}
        <div className="flex overflow-hidden rounded-full border border-canvas-line text-[10px]">
          {(['en', 'zh'] as PromptLang[]).map((l) => {
            const has = l === 'en' ? avail === 'both' || avail === 'en' : avail === 'both' || avail === 'zh';
            return (
              <button key={l} type="button" aria-pressed={lang === l} onClick={() => p.onLangChange(l)}
                className={`nodrag px-2 py-0.5 ${lang === l ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]' : 'text-canvas-muted'} ${has ? '' : 'opacity-40'}`}>
                {l === 'en' ? 'EN' : '中'}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-2.5 py-2">
        {entry.thumbs.length > 0 && !entry.slides && (
          <div className="flex gap-1.5">{entry.thumbs.map((th) => <img key={th.url} src={thumb(th.url)} alt="" className="h-14 w-14 rounded-lg object-cover" />)}</div>
        )}
        {entry.slides ? (
          <div>
            <div className={BLOCK_LABEL}>{t('canvas.library.slides', 'Slides')}<span className="font-normal normal-case tracking-normal">{t('canvas.library.slidesHint', 'click one to preview · each inserts on its own')}</span></div>
            <div className="flex flex-col gap-1">
              {entry.slides.map((s) => {
                const st = promptText(s, lang);
                const has = !!st.positive;
                const on = slide?.name === s.name;
                return (
                  <div key={s.name} data-testid="library-prompt-slide" data-active={on ? 'true' : 'false'} onClick={() => p.onSlideChange(s.name)}
                    className={`nodrag grid cursor-pointer grid-cols-[40px_1fr_auto] items-center gap-2 rounded-lg p-1.5 ${on ? 'bg-[var(--accent-soft)] ring-1 ring-inset ring-[var(--accent-border)]' : 'bg-canvas-card'} ${has ? '' : 'opacity-60'}`}>
                    {s.url ? <img src={thumb(s.url)} alt="" className="h-10 w-10 rounded-md object-cover" /> : <span className="block h-10 w-10 rounded-md bg-canvas-page" />}
                    <div className="min-w-0">
                      <p className={`m-0 line-clamp-2 font-mono text-[10.5px] ${has ? 'text-canvas-text' : 'text-canvas-muted'}`}>{has ? st.positive : t('canvas.library.noPromptOnSlide', 'No prompt on this slide')}</p>
                      <small className="text-[9.5px] text-canvas-muted">{s.name}</small>
                    </div>
                    <button type="button" className={`${BTN} px-2 py-0.5 text-[10.5px]`} disabled={!has || !p.canAct} onClick={(e) => { e.stopPropagation(); p.onSlideChange(s.name); p.onInsert(s.name); }}>{t('canvas.library.insert', 'Insert')}</button>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div>
            <div className={BLOCK_LABEL}>{t('canvas.library.positive', 'Positive')}</div>
            <pre data-testid="library-prompt-positive" className="m-0 whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed text-canvas-text">{text.positive || t('canvas.library.noPositive', 'No positive prompt')}</pre>
          </div>
        )}
        {text.negative && (
          <div>
            <div className={BLOCK_LABEL}>{t('canvas.library.negative', 'Negative')}{slide && <span className="font-normal normal-case tracking-normal">· {slide.name}</span>}</div>
            <pre data-testid="library-prompt-negative" className="m-0 whitespace-pre-wrap font-mono text-[11.5px] text-canvas-muted">{text.negative}</pre>
          </div>
        )}
        {chips.length > 0 && (
          <div>
            <div className={BLOCK_LABEL}>{t('canvas.library.params', 'Params')}<span className="font-normal normal-case tracking-normal">{t('canvas.library.paramsHint', 'applied only by Apply all')}</span></div>
            <div data-testid="library-prompt-params" className="flex flex-wrap gap-1">{chips.map((c) => <span key={c} className="rounded-md border border-canvas-line px-1.5 font-mono text-[10.5px] text-canvas-text">{c}</span>)}</div>
          </div>
        )}
        {entry.origin === 'captioned' && <p className="m-0 text-[10.5px] text-canvas-muted">{t('canvas.library.captionedNote', 'This text describes the picture, it did not make it')}</p>}
      </div>
      <div className="flex items-center gap-1.5 border-t border-canvas-line px-2.5 py-2">
        <button type="button" className={`${BTN} bg-[var(--accent-text)] text-white`} disabled={!canInsert} onClick={() => p.onInsert(slide?.name)}>{insertLabel}</button>
        <button type="button" className={BTN} disabled={!canInsert} onClick={() => p.onApplyAll(slide?.name)}>{applyLabel}</button>
        {entry.form !== 'template' && <button type="button" className={`${BTN} border-transparent`} disabled={p.canSaveAsTemplate === false} onClick={p.onSaveAsTemplate}>{t('canvas.library.saveAsTemplate', 'Save as template…')}</button>}
        <span className="flex-1" />
        <span className="text-[10.5px] text-canvas-muted">{p.canAct ? '↵ · ⇧↵' : p.actHint}</span>
      </div>
    </div>
  );
}
