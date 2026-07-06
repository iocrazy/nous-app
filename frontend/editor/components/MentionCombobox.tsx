/**
 * MentionCombobox — the @ entity picker popup (spec v3 §3.2 / §2.4, Task 8).
 *
 * A small floating LISTBOX that appears at the caret when the writer types `@`
 * (or focuses a character cue). Candidates are the current script's distinct
 * character names (Phase 1 has no separate entity table — the CAST derived from
 * character cues IS the entity list). Filtering is case-insensitive substring.
 *
 * This component is deliberately PRESENTATIONAL and CONTROLLED. It owns no
 * keyboard state: the active option index is supplied by SceneBlock (which reads
 * ARROW/ENTER/ESC from the focused contentEditable line, keeping a single keydown
 * source). SceneBlock also owns the WAI-ARIA combobox wiring — `role=combobox` +
 * `aria-controls` + `aria-activedescendant` live on the focused line, NOT here —
 * so screen readers announce the active option (PR-F3 review carry-over). This
 * popup only renders the `role=listbox` and its options. Mouse selection uses
 * onMouseDown + preventDefault so clicking an option never blurs the line.
 */
import { useMemo, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';

export interface MentionComboboxProps {
  candidates: string[];
  query: string;
  /** id shared with the focused line's aria-controls; options derive their ids from it. */
  listboxId: string;
  /** Active option index, owned by SceneBlock (the keyboard source). */
  activeIndex: number;
  onSelect: (name: string) => void;
  /** Mouse hover moves the active option so keyboard + pointer stay in sync. */
  onHover: (index: number) => void;
  /** Optional caret-anchored position; omitted in tests (jsdom has no layout). */
  position?: { top: number; left: number };
}

/** Case-insensitive substring filter over the CAST names (shared with SceneBlock). */
export function filterMentionCandidates(candidates: string[], query: string): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return candidates;
  return candidates.filter((c) => c.toLowerCase().includes(q));
}

export function MentionCombobox({
  candidates,
  query,
  listboxId,
  activeIndex,
  onSelect,
  onHover,
  position,
}: MentionComboboxProps) {
  const { t } = useTranslation();
  const filtered = useMemo(() => filterMentionCandidates(candidates, query), [candidates, query]);

  const style: CSSProperties = position
    ? { position: 'absolute', top: position.top, left: position.left }
    : {};

  return (
    <div className="mh-mention-pop" data-testid="mention-combobox" style={style}>
      {filtered.length === 0 ? (
        <div className="mh-mention-empty" role="note">
          {t('editor.mentionNoMatch')}
        </div>
      ) : (
        <ul
          className="mh-mention-list"
          role="listbox"
          id={listboxId}
          aria-label={t('editor.mentionListLabel')}
        >
          {filtered.map((name, i) => (
            <li
              key={name}
              id={`${listboxId}-opt-${i}`}
              role="option"
              aria-selected={i === activeIndex}
              className={`mh-mention-opt${i === activeIndex ? ' active' : ''}`}
              // preventDefault keeps focus on the editable line while selecting.
              onMouseDown={(e) => {
                e.preventDefault();
                onSelect(name);
              }}
              onMouseEnter={() => onHover(i)}
            >
              {name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
