// frontend/components/prompts/PromptTags.tsx
//
// The two little markers every prompt row carries: what SHAPE it is (template
// / picture / album) and where its text CAME FROM (typed / extracted from a
// file's metadata / written by a captioner).
//
// They are icons, not words in pills. The words did not fit: a prompt card is
// ~300px wide and the title, both markers and a thumbnail share its top row,
// so CJK labels wrapped mid-word — 「单 图」, 「AI 打 标」, 「模 板」 each
// broke across two lines and the row grew a second storey. An icon is one
// glyph at any locale and cannot wrap.
//
// What the icon must NOT drop is the NAME. A pictogram with no accessible
// name is a marker only sighted users who already know the system can read,
// so every tag here carries `role="img"` + `aria-label` (screen readers) and
// `title` (hover). The words are still there; they are just not taking up
// room until asked for.
import React from 'react';
import { useTranslation } from 'react-i18next';
import { FileText, Image, Images, PenLine, ScanLine, Sparkles } from 'lucide-react';
import type { PromptForm, PromptOrigin } from '../../services/promptsService';

const FORM_ICON: Record<PromptForm, React.ComponentType<{ size?: number; className?: string }>> = {
  template: FileText,
  image: Image,
  album: Images,
};

/** Semantic tokens only — the same three the pills used, now on the glyph. */
const FORM_CLASS: Record<PromptForm, string> = {
  template: 'text-ok',
  image: 'text-info',
  album: 'text-warn',
};

const FORM_LABEL: Record<PromptForm, [string, string]> = {
  template: ['prompts.form.template', 'Template'],
  image: ['prompts.form.image', 'Image'],
  album: ['prompts.form.album', 'Album'],
};

const ORIGIN_ICON: Record<PromptOrigin, React.ComponentType<{ size?: number; className?: string }>> = {
  typed: PenLine,
  extracted: ScanLine,
  captioned: Sparkles,
};

const ORIGIN_LABEL: Record<PromptOrigin, [string, string]> = {
  typed: ['prompts.origin.typed', 'Typed'],
  extracted: ['prompts.origin.extracted', 'Extracted'],
  captioned: ['prompts.origin.captioned', 'Captioned'],
};

export function FormTag({ form }: { form: PromptForm }): React.ReactElement {
  const { t } = useTranslation();
  const Icon = FORM_ICON[form];
  const label = t(FORM_LABEL[form][0], FORM_LABEL[form][1]);
  return (
    <span
      data-testid="prompt-form-tag"
      data-form={form}
      role="img"
      aria-label={label}
      title={label}
      className={`shrink-0 ${FORM_CLASS[form]}`}
    >
      <Icon size={13} />
    </span>
  );
}

/**
 * Origin, plus the tool/model short name for extracted rows (they made the
 * picture).
 *
 * The tool name stays TEXT while the origin becomes an icon, and the split is
 * deliberate: "extracted" is one of three known states an icon can stand for,
 * but `gpt-6-astra` is data — there is no glyph for it, and dropping it to
 * hover would hide the one thing on the row that says which model to go back
 * to. It gets `whitespace-nowrap` because that is the piece that can still
 * wrap now that the label is gone.
 */
export function OriginTag({
  origin,
  params,
}: {
  origin: PromptOrigin | null;
  params?: Record<string, unknown> | null;
}): React.ReactElement | null {
  const { t } = useTranslation();
  if (!origin) return null;
  const Icon = ORIGIN_ICON[origin];
  const label = t(ORIGIN_LABEL[origin][0], ORIGIN_LABEL[origin][1]);
  const tool =
    origin === 'extracted'
      ? [params?.tool, params?.model].find((v) => typeof v === 'string' && v)
      : null;
  return (
    <span
      data-testid="prompt-origin-tag"
      data-origin={origin}
      title={tool ? `${label} · ${String(tool)}` : label}
      className="inline-flex shrink-0 items-center gap-1 text-content-3"
    >
      {/* The label rides the icon, not the wrapper: with the tool name beside
          it, one aria-label on the whole span would read the model name twice. */}
      <span role="img" aria-label={label} className="inline-flex">
        <Icon size={13} />
      </span>
      {tool && (
        <span className="whitespace-nowrap text-[9.5px] leading-4">{String(tool)}</span>
      )}
    </span>
  );
}
