// frontend/components/prompts/SaveAsTemplateDialog.tsx
//
// Spec §3.5 — the promotion path. Same TemplateForm the panel embeds inline.
import React, { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useOptionalToast } from '../Toast';
import { promptText, saveAsTemplate, textForSide, type PromptEntry, type PromptLang } from '../../services/promptsService';
import { TemplateForm, type TemplateFormValue } from './TemplateForm';

export interface SaveAsTemplateDialogProps {
  scopeId: string;
  entry: PromptEntry;
  /** Album flow: the slides whose text the dialog should offer. */
  slideNames?: string[];
  lang: PromptLang;
  onClose: () => void;
  onSaved: (assetId: string) => void;
}

/** Prefill: an image ticks itself; an album joins the ticked slides' text as
 *  numbered lines and ticks the album resource (slides are not resources). */
export function initialTemplateValue(entry: PromptEntry, slideNames: string[] | undefined, lang: PromptLang): TemplateFormValue {
  if (entry.form === 'album' && entry.slides) {
    // Number AFTER dropping the textless ones. A textless slide is offered
    // like any other (spec §3.1 keeps it listed so "5 of 6 have text" stays
    // honest), so it can be ticked — numbering first left a hole: "1. …",
    // "3. …".
    const picked = entry.slides
      .filter((s) => (slideNames ? slideNames.includes(s.name) : true))
      .map((s) => promptText(s, lang).positive)
      .filter((line) => line.trim().length > 0);
    const positive = picked.map((line, i) => `${i + 1}. ${line}`).join('\n');
    return { title: entry.title, group: '', positive, negative: '', exampleIds: entry.source.id ? [entry.source.id] : [] };
  }
  const text = promptText(entry, lang);
  return {
    title: entry.title,
    group: '',
    positive: text.positive,
    negative: text.negative ?? '',
    exampleIds: entry.form === 'image' && entry.source.id ? [entry.source.id] : [],
  };
}

/** Which language side the prefill actually read from — i.e. the columns the
 *  promoted template must be written into (see `textForSide`). For an album it
 *  is the first ticked slide that supplied any text. */
export function shownSide(entry: PromptEntry, slideNames: string[] | undefined, lang: PromptLang): PromptLang | null {
  if (entry.form === 'album' && entry.slides) {
    for (const s of entry.slides) {
      if (slideNames && !slideNames.includes(s.name)) continue;
      const side = promptText(s, lang).shownLang;
      if (side) return side;
    }
    return null;
  }
  return promptText(entry, lang).shownLang;
}

export function SaveAsTemplateDialog({ scopeId, entry, slideNames, lang, onClose, onSaved }: SaveAsTemplateDialogProps): React.ReactElement {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const [value, setValue] = useState<TemplateFormValue>(() => initialTemplateValue(entry, slideNames, lang));
  const [busy, setBusy] = useState(false);
  // One tile, and only when a resource row stands behind it: an album's slides
  // are not resources, so the album itself is what gets ticked. An id of ''
  // ("New template") offers nothing — a tile the prefill above would never
  // tick is a tile that cannot be saved.
  const examples = useMemo(
    () => (entry.form !== 'template' && entry.source.id ? [{ id: entry.source.id, url: entry.thumbs[0]?.url ?? null }] : []),
    [entry],
  );
  const canSave = value.title.trim().length > 0 && value.positive.trim().length > 0 && !busy;

  const submit = async () => {
    if (!canSave) return;
    setBusy(true);
    try {
      const { assetId } = await saveAsTemplate(scopeId, {
        title: value.title, group: value.group, exampleResourceIds: value.exampleIds,
        ...textForSide(shownSide(entry, slideNames, lang), value.positive, value.negative),
      });
      toast?.addToast(t('prompts.save.saved', 'Saved to Mine'), 'success');
      onSaved(assetId);
    } catch (err) {
      console.error('[SaveAsTemplateDialog] save failed:', err);
      const code = (err as { code?: string })?.code ?? 'unknown';
      toast?.addToast(t('prompts.save.failed', { code, defaultValue: 'Could not save: {{code}}' }), 'error');
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }} data-testid="save-as-template-dialog">
      <div role="dialog" aria-modal="true" aria-labelledby="save-as-template-heading" className="flex w-[520px] max-h-[80vh] flex-col rounded-xl border border-line bg-card shadow-xl">
        <div id="save-as-template-heading" className="flex items-center border-b border-line px-4 py-2 text-[13px] font-semibold text-content">
          {entry.key === 'template:new' ? t('prompts.save.newTitle', 'New Template') : t('prompts.save.title', 'Save as Template')}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <TemplateForm value={value} onChange={setValue} examples={examples} groups={[]} disabled={busy} />
        </div>
        <div className="flex items-center gap-2 border-t border-line px-4 py-2">
          <button type="button" className="rounded-lg bg-accent px-3 py-1 text-[12px] font-medium text-card disabled:opacity-40" disabled={!canSave} onClick={() => void submit()}>{t('prompts.save.submit', 'Save to Mine')}</button>
          <button type="button" className="rounded-lg border border-line px-3 py-1 text-[12px]" onClick={onClose}>{t('common.cancel', 'Cancel')}</button>
          <span className="flex-1" />
          <span className="text-[10.5px] text-content-3">{t('prompts.save.filesStay', 'Pictures stay where they are')}</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
