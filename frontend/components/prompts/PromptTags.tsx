import React from 'react';
import { useTranslation } from 'react-i18next';
import type { PromptForm, PromptOrigin } from '../../services/promptsService';

const FORM_CLASS: Record<PromptForm, string> = {
  template: 'bg-ok-soft text-ok',
  image: 'bg-info-soft text-info',
  album: 'bg-warn-soft text-warn',
};

export function FormTag({ form }: { form: PromptForm }): React.ReactElement {
  const { t } = useTranslation();
  const label =
    form === 'template' ? t('prompts.form.template', 'Template')
    : form === 'image' ? t('prompts.form.image', 'Image')
    : t('prompts.form.album', 'Album');
  return <span data-testid="prompt-form-tag" className={`rounded-full px-1.5 text-[9.5px] leading-4 ${FORM_CLASS[form]}`}>{label}</span>;
}

/** Origin, plus the tool/model short name for extracted rows (they made the picture). */
export function OriginTag({ origin, params }: { origin: PromptOrigin | null; params?: Record<string, unknown> | null }): React.ReactElement | null {
  const { t } = useTranslation();
  if (!origin) return null;
  const tool = origin === 'extracted' ? [params?.tool, params?.model].find((v) => typeof v === 'string' && v) : null;
  const label =
    origin === 'typed' ? t('prompts.origin.typed', 'Typed')
    : origin === 'extracted' ? t('prompts.origin.extracted', 'Extracted')
    : t('prompts.origin.captioned', 'Captioned');
  return (
    <span data-testid="prompt-origin-tag" className={`rounded-full bg-island-2 px-1.5 text-[9.5px] leading-4 text-content-3 ${origin === 'captioned' ? 'italic' : ''}`}>
      {tool ? `${label} · ${String(tool)}` : label}
    </span>
  );
}
