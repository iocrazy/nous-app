// components/Distribution/CoverStudio/PromptMentionInput.tsx
//
// The prompt box with "@" pictures. Typing `@` opens a small list of the
// pictures being sent to the model (person, frames, templates) plus a way
// into the template library; choosing one drops a `@{label}` token at the
// caret. The parent expands tokens into positional references before the
// prompt is sent (promptMentions.ts) — the model gets images by position.

import React, { useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AtSign, Library } from 'lucide-react';

import { getApiUrl } from '../../../utils/apiConfig';
import type { CoverReference } from './coverReferences';
import { insertMention, mentionLabel, mentionToken } from './promptMentions';
import './cover-studio.css';

interface Props {
  value: string;
  onChange: (next: string) => void;
  /** Pictures that can be @-mentioned, in send order. */
  refs: CoverReference[];
  /** Open the template picker; the picked template is mentioned once added. */
  onOpenLibrary?: () => void;
  placeholder?: string;
  maxLength?: number;
}

export function PromptMentionInput({
  value,
  onChange,
  refs,
  onOpenLibrary,
  placeholder,
  maxLength = 500,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const [menu, setMenu] = useState<{ query: string } | null>(null);

  const options = useMemo(() => {
    const q = (menu?.query ?? '').toLowerCase();
    return refs.filter((r) => mentionLabel(r).toLowerCase().includes(q));
  }, [refs, menu]);

  /** Re-derive the menu from the text around the caret. */
  const syncMenu = (text: string, caret: number) => {
    const before = text.slice(0, caret);
    const at = before.lastIndexOf('@');
    if (at < 0 || before.slice(at).includes('}') || /\s/.test(before.slice(at + 1))) {
      setMenu(null);
      return;
    }
    setMenu({ query: before.slice(at + 1) });
  };

  const pick = (ref: CoverReference) => {
    const area = areaRef.current;
    const caret = area?.selectionStart ?? value.length;
    const next = insertMention(value, caret, mentionToken(ref));
    onChange(next.text);
    setMenu(null);
    requestAnimationFrame(() => {
      area?.focus();
      area?.setSelectionRange(next.caret, next.caret);
    });
  };

  return (
    <div className="cs-mention">
      <textarea
        ref={areaRef}
        className="cs-instructions"
        rows={3}
        maxLength={maxLength}
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          onChange(e.target.value);
          syncMenu(e.target.value, e.target.selectionStart ?? e.target.value.length);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && menu) {
            e.preventDefault();
            setMenu(null);
          }
          if (e.key === 'Enter' && menu && options[0]) {
            e.preventDefault();
            pick(options[0]);
          }
        }}
        onBlur={() => setTimeout(() => setMenu(null), 150)}
        data-testid="cover-instructions"
      />
      <button
        type="button"
        className="cs-mention-at"
        title={t('distribution.coverStudio.mentionHint', 'Type @ to point at a picture')}
        aria-label={t('distribution.coverStudio.mentionHint', 'Type @ to point at a picture')}
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => {
          const area = areaRef.current;
          const caret = area?.selectionStart ?? value.length;
          const next = `${value.slice(0, caret)}@${value.slice(caret)}`;
          onChange(next);
          setMenu({ query: '' });
          requestAnimationFrame(() => {
            area?.focus();
            area?.setSelectionRange(caret + 1, caret + 1);
          });
        }}
        data-testid="cover-mention-at"
      >
        <AtSign size={13} />
      </button>

      {menu && (
        <div className="cs-mention-menu" role="listbox" data-testid="cover-mention-menu">
          {options.length === 0 && refs.length === 0 && (
            <div className="cs-empty">
              {t('distribution.coverStudio.mentionEmpty', 'No pictures yet — grab a frame or add a template first.')}
            </div>
          )}
          {options.map((r) => (
            <button
              key={r.genId}
              type="button"
              role="option"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(r)}
              data-testid={`cover-mention-${r.genId}`}
            >
              <img src={`${getApiUrl()}${r.url}`} alt="" />
              <span>@{mentionLabel(r)}</span>
            </button>
          ))}
          {onOpenLibrary && (
            <button
              type="button"
              role="option"
              className="lib"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                setMenu(null);
                onOpenLibrary();
              }}
              data-testid="cover-mention-library"
            >
              <Library size={13} />
              <span>{t('distribution.coverStudio.mentionLibrary', 'From the template library…')}</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}
