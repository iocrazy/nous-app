/**
 * MentionCombobox — the @ entity picker (spec v3 §3.2 / §2.4, Task 8).
 *
 * A small ARIA combobox that floats at the caret when the writer types `@` (or
 * focuses a character cue). Candidates are the current script's distinct
 * character names (Phase 1 has no separate entity table — the CAST derived from
 * character cues IS the entity list). Filtering is case-insensitive substring.
 *
 * Keyboard ownership is deliberately split: the popup renders and tracks its own
 * active option, but the ARROW/ENTER/ESC keystrokes originate in the
 * contentEditable line, so SceneBlock intercepts them and drives this component
 * through the imperative handle (`move`, `confirm`). That keeps a single keydown
 * source (the editable) and avoids a competing global listener. Mouse selection
 * uses onMouseDown + preventDefault so clicking an option never blurs the line.
 */
import {
  forwardRef,
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useState,
  type CSSProperties,
} from 'react';
import { useTranslation } from 'react-i18next';

export interface MentionComboboxHandle {
  /** Move the active option by delta (wraps within the filtered list). */
  move(delta: number): void;
  /** Select the active option; returns true if a selection was made. */
  confirm(): boolean;
}

export interface MentionComboboxProps {
  candidates: string[];
  query: string;
  onSelect: (name: string) => void;
  onClose: () => void;
  /** Optional caret-anchored position; omitted in tests (jsdom has no layout). */
  position?: { top: number; left: number };
}

function filterCandidates(candidates: string[], query: string): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return candidates;
  return candidates.filter((c) => c.toLowerCase().includes(q));
}

export const MentionCombobox = forwardRef<MentionComboboxHandle, MentionComboboxProps>(
  function MentionCombobox({ candidates, query, onSelect, position }, ref) {
    const { t } = useTranslation();
    const listId = useId();
    const filtered = useMemo(() => filterCandidates(candidates, query), [candidates, query]);
    const [active, setActive] = useState(0);

    // Clamp the active option back into range whenever the filtered set changes
    // (typing narrows the list); keep it on the first match.
    useEffect(() => {
      setActive(0);
    }, [query, candidates]);

    useImperativeHandle(
      ref,
      () => ({
        move(delta: number) {
          setActive((prev) => {
            if (filtered.length === 0) return 0;
            return (prev + delta + filtered.length) % filtered.length;
          });
        },
        confirm() {
          if (filtered.length === 0 || active < 0 || active >= filtered.length) return false;
          onSelect(filtered[active]);
          return true;
        },
      }),
      [filtered, active, onSelect],
    );

    const style: CSSProperties = position
      ? { position: 'absolute', top: position.top, left: position.left }
      : {};

    return (
      <div
        className="mh-mention-pop"
        data-testid="mention-combobox"
        role="combobox"
        aria-label={t('editor.mentionListLabel')}
        aria-expanded="true"
        aria-haspopup="listbox"
        aria-controls={listId}
        aria-activedescendant={
          filtered.length > 0 ? `${listId}-opt-${active}` : undefined
        }
        style={style}
      >
        {filtered.length === 0 ? (
          <div className="mh-mention-empty" role="note">
            {t('editor.mentionNoMatch')}
          </div>
        ) : (
          <ul className="mh-mention-list" role="listbox" id={listId} aria-label={t('editor.mentionListLabel')}>
            {filtered.map((name, i) => (
              <li
                key={name}
                id={`${listId}-opt-${i}`}
                role="option"
                aria-selected={i === active}
                className={`mh-mention-opt${i === active ? ' active' : ''}`}
                // preventDefault keeps focus on the editable line while selecting.
                onMouseDown={(e) => {
                  e.preventDefault();
                  onSelect(name);
                }}
                onMouseEnter={() => setActive(i)}
              >
                {name}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  },
);
