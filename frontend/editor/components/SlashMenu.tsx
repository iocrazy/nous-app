/**
 * SlashMenu — Notion/laper-style block-type picker, opened by typing `/` at
 * the start of a block. Purely presentational (mirrors MentionCombobox): the
 * owning SceneBlock holds the open/query/active state and the keyboard
 * machine; this renders the filtered listbox at the caret-anchored position.
 */
import type { CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import type { ElementType } from '../types';

export interface SlashItem {
  type: ElementType;
  labelKey: string;
  glyph: string;
}

/** Same vocabulary + labels as the toolbar (minus Scene — types retype the
 *  CURRENT block; inserting a scene is the toolbar's job). */
export const SLASH_ITEMS: SlashItem[] = [
  { type: 'action', labelKey: 'editor.toolbarAction', glyph: '—' },
  { type: 'character', labelKey: 'editor.toolbarCharacter', glyph: 'A' },
  { type: 'dialogue', labelKey: 'editor.toolbarDialogue', glyph: '"' },
  { type: 'paren', labelKey: 'editor.toolbarParen', glyph: '()' },
  { type: 'transition', labelKey: 'editor.toolbarTransition', glyph: '▷' },
  { type: 'comment', labelKey: 'editor.toolbarComment', glyph: '✎' },
  { type: 'subtitle', labelKey: 'editor.toolbarSubtitle', glyph: '▾' },
];

/** Filter the vocabulary on the text typed after `/` (label or type name). */
export function filterSlashItems(
  items: SlashItem[],
  query: string,
  label: (key: string) => string,
): SlashItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter(
    (it) => it.type.includes(q) || label(it.labelKey).toLowerCase().includes(q),
  );
}

export function SlashMenu({
  items,
  activeIndex,
  listboxId,
  position,
  onSelect,
  onHover,
}: {
  items: SlashItem[];
  activeIndex: number;
  listboxId: string;
  /** Caret-anchored position; omitted in tests (jsdom has no layout). */
  position?: { top: number; left: number };
  onSelect: (type: ElementType) => void;
  onHover: (index: number) => void;
}) {
  const { t } = useTranslation();
  const style: CSSProperties = position
    ? { position: 'absolute', top: position.top, left: position.left }
    : {};

  return (
    <div className="mh-slash-menu" data-testid="slash-menu" style={style}>
      {items.length === 0 ? (
        <div className="mh-slash-empty" role="note">
          {t('editor.slashNoMatch')}
        </div>
      ) : (
        <div id={listboxId} role="listbox" aria-label={t('editor.slashTitle')}>
          {items.map((it, i) => (
            <button
              key={it.type}
              type="button"
              id={`${listboxId}-opt-${i}`}
              role="option"
              aria-selected={i === activeIndex}
              className={`mh-slash-item${i === activeIndex ? ' active' : ''}`}
              // preventDefault so the click never steals focus from the line.
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onSelect(it.type)}
              onMouseEnter={() => onHover(i)}
            >
              <span className="mh-slash-glyph" aria-hidden="true">
                {it.glyph}
              </span>
              {t(it.labelKey)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
