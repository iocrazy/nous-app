/**
 * HeadingSelect — a LIGHT, self-contained dropdown for the scene head row's
 * INT/EXT + time-of-day pickers (laper parity).
 *
 * Why not UiSelect: the app-wide UiSelect is a DARK menu whose panel width tracks
 * its trigger. The heading triggers show only a short token (`INT`) or a
 * placeholder, so a UiSelect menu renders tiny and CLIPS the option labels
 * ("D.. N.. D.."), and its dark chrome clashes with the light script sheet. This
 * component instead renders a chip-style trigger with an absolutely-positioned
 * LIGHT popup (the `.mh-mention-pop` DNA) sized to its OWN content, so every
 * option label is fully legible and the palette matches the sheet.
 *
 * The component is fully CONTROLLED: `value` in, `onChange(next)` out. The unset
 * option maps to the empty string (the meta field's "no value" sentinel). Tab
 * from an OPEN popup commits the active option and calls `onTabNext` so the
 * writer flows INT/EXT → location (laper's "Tab → Switch to location"); Tab from
 * a CLOSED trigger is left to the browser's native focus order.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';

export interface HeadingSelectProps {
  /** Current value; '' means unset. */
  value: string;
  /** Selectable tokens (e.g. INT/EXT/INT-EXT or DAY/NIGHT/…). */
  options: string[];
  /** Shown on the trigger when `value` is empty. */
  placeholder: string;
  /** Accessible name on the trigger button (tests query by this). */
  ariaLabel: string;
  onChange: (v: string) => void;
  /** Focus the trigger on mount (the first head-row field on entering edit). */
  autoFocus?: boolean;
  /** Called after Tab commits from the open popup — advance to the next field. */
  onTabNext?: () => void;
  /** laper-parity hint shown at the top of the open popup, e.g. "Switch to
   *  location" — paired with a [Tab] key glyph. Omitted → no hint row. */
  tabHint?: string;
}

export function HeadingSelect({
  value,
  options,
  placeholder,
  ariaLabel,
  onChange,
  autoFocus,
  onTabNext,
  tabHint,
}: HeadingSelectProps) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  // The unset option ('') sits at the top; the rest follow in order.
  const entries = useMemo(() => ['', ...options], [options]);

  // Focus the trigger on mount when requested (head row just entered edit mode).
  useEffect(() => {
    if (autoFocus) triggerRef.current?.focus();
  }, [autoFocus]);

  // Outside-click closes the popup — the same armed-while-open document listener
  // SceneBlock uses for the mention/slash pickers (see `onDocMouseDown` there).
  useEffect(() => {
    if (!open) return;
    const onDocMouseDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [open]);

  const openMenu = useCallback(() => {
    const idx = entries.indexOf(value);
    setActive(idx >= 0 ? idx : 0);
    setOpen(true);
  }, [entries, value]);

  // Commit a value from the keyboard/mouse and return focus to the trigger.
  const commit = useCallback(
    (v: string) => {
      onChange(v);
      setOpen(false);
      triggerRef.current?.focus();
    },
    [onChange],
  );

  const handleKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (!open) {
      // Closed: only open on Down/Enter/Space; Tab stays native (→ next field).
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActive((p) => (p + 1) % entries.length);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActive((p) => (p - 1 + entries.length) % entries.length);
        break;
      case 'Enter':
      case ' ':
        e.preventDefault();
        commit(entries[active] ?? '');
        break;
      case 'Escape':
        e.preventDefault();
        setOpen(false);
        break;
      case 'Tab':
        // Commit the active option then advance to the next field (laper flow).
        e.preventDefault();
        onChange(entries[active] ?? '');
        setOpen(false);
        onTabNext?.();
        break;
      default:
        break;
    }
  };

  return (
    <div className="mh-heading-select" ref={rootRef}>
      <button
        type="button"
        ref={triggerRef}
        className="mh-scene-select"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        data-placeholder={value ? undefined : 'true'}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={handleKeyDown}
      >
        {value || placeholder}
      </button>
      {open && (
        <div className="mh-heading-pop" role="listbox" aria-label={ariaLabel}>
          {tabHint && (
            <div className="mh-heading-hint" aria-hidden="true">
              <kbd className="mh-pop-kbd">Tab</kbd>
              <span>{tabHint}</span>
            </div>
          )}
          {entries.map((opt, i) => (
            <div
              key={opt || '__unset__'}
              role="option"
              aria-selected={opt === value}
              className={`mh-heading-opt${i === active ? ' active' : ''}`}
              // preventDefault so clicking never blurs the head row (which would
              // otherwise collapse it back to read mode before the commit lands).
              onMouseDown={(e) => {
                e.preventDefault();
                commit(opt);
              }}
              onMouseEnter={() => setActive(i)}
            >
              {opt || '—'}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
