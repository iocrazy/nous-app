/**
 * ElementToolbar — the horizontal, mode-slot floating pill above the paper
 * (R2-A Final's defining chrome; spec v3 §3.2).
 *
 * The toolbar's SLOTS swap by mode, and the slot concept itself must always be
 * visible — that is the finalized design idea:
 *  - Script mode: "Scene" (inserts a new scene block) + the 7 element types.
 *    Clicking a type retypes the focused line, or — with no line focused — sets
 *    the next-insert type. The active pill tracks the cursor element's type.
 *  - Outline mode: Body / H1 / H2 / H3 / Quote / Bold / Italic / Rule, all
 *    rendered but disabled (outline editing is Phase 2). They stay visible so
 *    the mode-slot model reads correctly.
 * Roving tabindex keeps the whole bar reachable with one Tab stop + arrow keys.
 */
import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { ElementType } from '../types';
import type { EditorMode } from '../useEditorState';

interface ScriptItem {
  key: string;
  labelKey: string;
  glyph: string;
  /** Undefined = the "Scene" action item (insert), not a type toggle. */
  type?: ElementType;
}

const SCRIPT_ITEMS: ScriptItem[] = [
  { key: 'scene', labelKey: 'editor.toolbarScene', glyph: '▢' },
  { key: 'action', labelKey: 'editor.toolbarAction', glyph: '—', type: 'action' },
  { key: 'character', labelKey: 'editor.toolbarCharacter', glyph: 'A', type: 'character' },
  { key: 'paren', labelKey: 'editor.toolbarParen', glyph: '()', type: 'paren' },
  { key: 'dialogue', labelKey: 'editor.toolbarDialogue', glyph: '"', type: 'dialogue' },
  { key: 'transition', labelKey: 'editor.toolbarTransition', glyph: '▷', type: 'transition' },
  { key: 'comment', labelKey: 'editor.toolbarComment', glyph: '✎', type: 'comment' },
  { key: 'subtitle', labelKey: 'editor.toolbarSubtitle', glyph: '▾', type: 'subtitle' },
];

const OUTLINE_ITEMS: { key: string; labelKey: string; glyph: string }[] = [
  { key: 'body', labelKey: 'editor.toolbarBody', glyph: '¶' },
  { key: 'h1', labelKey: 'editor.toolbarH1', glyph: 'H1' },
  { key: 'h2', labelKey: 'editor.toolbarH2', glyph: 'H2' },
  { key: 'h3', labelKey: 'editor.toolbarH3', glyph: 'H3' },
  { key: 'quote', labelKey: 'editor.toolbarQuote', glyph: '"' },
  { key: 'bold', labelKey: 'editor.toolbarBold', glyph: 'B' },
  { key: 'italic', labelKey: 'editor.toolbarItalic', glyph: 'I' },
  { key: 'rule', labelKey: 'editor.toolbarRule', glyph: '—' },
];

export interface ElementToolbarProps {
  mode: EditorMode;
  activeType: ElementType | null;
  onSelectType: (type: ElementType) => void;
  onInsertScene: () => void;
}

export function ElementToolbar({
  mode,
  activeType,
  onSelectType,
  onInsertScene,
}: ElementToolbarProps) {
  const { t } = useTranslation();
  const [rovingIndex, setRovingIndex] = useState(0);
  const itemsRef = useRef<Array<HTMLButtonElement | null>>([]);

  const count = mode === 'outline' ? OUTLINE_ITEMS.length : SCRIPT_ITEMS.length;

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent, index: number) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      e.preventDefault();
      const dir = e.key === 'ArrowRight' ? 1 : -1;
      const next = (index + dir + count) % count;
      setRovingIndex(next);
      itemsRef.current[next]?.focus();
    },
    [count],
  );

  if (mode === 'cover') return null;

  if (mode === 'outline') {
    return (
      <div className="mh-h-toolbar" role="toolbar" aria-label={t('editor.toolbar')}>
        {OUTLINE_ITEMS.map((item, i) => (
          <button
            type="button"
            key={item.key}
            ref={(el) => {
              itemsRef.current[i] = el;
            }}
            className="mh-h-item"
            disabled
            tabIndex={i === rovingIndex ? 0 : -1}
            onKeyDown={(e) => onKeyDown(e, i)}
          >
            <span className="mh-h-glyph" aria-hidden="true">
              {item.glyph}
            </span>
            {t(item.labelKey)}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="mh-h-toolbar" role="toolbar" aria-label={t('editor.toolbar')}>
      {SCRIPT_ITEMS.map((item, i) => {
        const isActive = item.type != null && item.type === activeType;
        return (
          <button
            type="button"
            key={item.key}
            ref={(el) => {
              itemsRef.current[i] = el;
            }}
            className={`mh-h-item${isActive ? ' active' : ''}`}
            aria-pressed={item.type != null ? isActive : undefined}
            tabIndex={i === rovingIndex ? 0 : -1}
            onKeyDown={(e) => onKeyDown(e, i)}
            onClick={() => {
              setRovingIndex(i);
              if (item.type) onSelectType(item.type);
              else onInsertScene();
            }}
          >
            <span className="mh-h-glyph" aria-hidden="true">
              {item.glyph}
            </span>
            {t(item.labelKey)}
          </button>
        );
      })}
    </div>
  );
}
