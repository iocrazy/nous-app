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
 *
 * laper-parity upgrade (user-approved design): the CHARACTER-CUE variant embeds
 * a real search input at the top of the panel and a kbd-hint footer, matching
 * the app-wide searchable dropdown DNA (UiSelect). Focus is NOT stolen on open
 * — typing in the line still filters (query = line text, the existing flow);
 * clicking the search box lets the writer type there instead, with the input's
 * own onKeyDown mirroring the line's nav semantics (Arrows/Enter/Tab/Escape).
 * Enter with no match commits the typed text as a NEW character name.
 */
import { useMemo, type CSSProperties, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Search } from 'lucide-react';

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
  /** Picker flavour: 'character' renders the embedded search input + Tab hint;
   *  'inline' keeps line-driven filtering (query typed after `@` in the line).
   *  Omitted (legacy callers/tests) → plain list, no search, no footer. */
  kind?: 'inline' | 'character';
  /** Search-input edits (character kind) — SceneBlock mirrors them into the
   *  shared mention.query so line-typing and box-typing stay one state. */
  onQueryChange?: (query: string) => void;
  /** Tab from the search input: abandon the cue, revert the block to action. */
  onTabAction?: () => void;
  /** Escape from the search input: close the popup and refocus the line. */
  onClose?: () => void;
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
  kind,
  onQueryChange,
  onTabAction,
  onClose,
}: MentionComboboxProps) {
  const { t } = useTranslation();
  const filtered = useMemo(() => filterMentionCandidates(candidates, query), [candidates, query]);

  const style: CSSProperties = position
    ? { position: 'absolute', top: position.top, left: position.left }
    : {};

  // The embedded input mirrors the line's keyboard semantics so the picker
  // behaves identically whichever surface holds the caret.
  const handleSearchKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (filtered.length > 0) onHover((activeIndex + 1) % filtered.length);
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (filtered.length > 0) onHover((activeIndex - 1 + filtered.length) % filtered.length);
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (filtered.length > 0 && activeIndex >= 0 && activeIndex < filtered.length) {
        onSelect(filtered[activeIndex]);
      } else if (query.trim()) {
        // No match → the typed text becomes a NEW character (laper behaviour).
        onSelect(query.trim());
      } else {
        onClose?.();
      }
      return;
    }
    if (e.key === 'Tab') {
      e.preventDefault();
      onTabAction?.();
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose?.();
    }
  };

  const searchable = kind === 'character';

  return (
    <div className="mh-mention-pop" data-testid="mention-combobox" style={style}>
      {searchable && (
        <div className="mh-mention-search">
          <Search size={13} aria-hidden="true" />
          <input
            type="text"
            value={query}
            placeholder={t('editor.cueSearchPlaceholder')}
            aria-label={t('editor.cueSearchLabel')}
            onChange={(e) => onQueryChange?.(e.target.value)}
            onKeyDown={handleSearchKeyDown}
          />
        </div>
      )}
      {filtered.length === 0 ? (
        <div className="mh-mention-empty" role="note">
          {searchable && query.trim()
            ? t('editor.cueCreateHint', { name: query.trim() })
            : t('editor.mentionNoMatch')}
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
      {kind && (
        <div className="mh-mention-hints">
          <span className="mh-mention-hint">
            <kbd className="mh-pop-kbd">Enter</kbd>
            {kind === 'character' ? t('editor.cueHintEnter') : t('editor.mentionHintEnter')}
          </span>
          {kind === 'character' && (
            <span className="mh-mention-hint">
              <kbd className="mh-pop-kbd">Tab</kbd>
              {t('editor.cueHintTab')}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
