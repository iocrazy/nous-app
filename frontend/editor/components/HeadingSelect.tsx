/**
 * HeadingSelect — a LIGHT, self-contained dropdown for the scene head row's
 * fields (laper parity).
 *
 * Two modes:
 *  - ENUM (default): a fixed option list (INT/EXT, DAY/NIGHT/…). The trigger
 *    shows the value or a placeholder; the popup lists the tokens.
 *  - SEARCHABLE (`searchable`): the location field. The popup embeds a
 *    "Search or create location…" input over the script's existing locations
 *    (`candidates`); typing filters, and a non-matching query offers a
 *    "Create <query>" row — pick an existing one or coin a new one, laper-style.
 *
 * Why not UiSelect: the app-wide UiSelect is a DARK menu whose panel width tracks
 * its trigger; the short heading triggers make it clip labels and clash with the
 * light sheet. This renders a flat chip trigger + a light popup (`.mh-heading-pop`
 * DNA) sized to its own content.
 *
 * Fully CONTROLLED: `value` in, `onChange(next)` out. Tab from an open popup
 * commits the active option and calls `onTabNext` so the writer flows
 * INT/EXT → location → time (laper's "Tab → Switch to …").
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { useTranslation } from 'react-i18next';

export interface HeadingSelectProps {
  /** Current value; '' means unset. */
  value: string;
  /** Selectable tokens for ENUM mode (e.g. INT/EXT or DAY/NIGHT/…). */
  options?: string[];
  /** Shown on the trigger when `value` is empty. */
  placeholder: string;
  /** Accessible name on the trigger button (tests query by this). */
  ariaLabel: string;
  onChange: (v: string) => void;
  /** Focus the trigger on mount (the first head-row field on entering edit). */
  autoFocus?: boolean;
  /** Called after Tab commits from the open popup — advance to the next field. */
  onTabNext?: () => void;
  /** laper-parity hint shown at the top of the open popup (paired with [Tab]). */
  tabHint?: string;
  /** SEARCHABLE mode (the location field): embed a search-or-create input. */
  searchable?: boolean;
  /** Existing values to offer in searchable mode (the script's locations). */
  candidates?: string[];
}

export function HeadingSelect({
  value,
  options = [],
  placeholder,
  ariaLabel,
  onChange,
  autoFocus,
  onTabNext,
  tabHint,
  searchable = false,
  candidates = [],
}: HeadingSelectProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  // ENUM: the unset option ('') sits on top, then the tokens.
  // SEARCHABLE: the case-insensitive-filtered candidates, plus a synthetic
  // "create" row when the trimmed query matches nothing exactly.
  const entries = useMemo<string[]>(() => {
    if (!searchable) return ['', ...options];
    const q = query.trim().toLowerCase();
    const filtered = candidates.filter((c) => c.toLowerCase().includes(q));
    const exact = candidates.some((c) => c.toLowerCase() === q);
    // The create row is represented by the raw query string; render decides.
    return q && !exact ? [...filtered, query.trim()] : filtered;
  }, [searchable, options, candidates, query]);

  const createIndex = useMemo(() => {
    if (!searchable) return -1;
    const q = query.trim().toLowerCase();
    const exact = candidates.some((c) => c.toLowerCase() === q);
    return q && !exact ? entries.length - 1 : -1;
  }, [searchable, candidates, query, entries.length]);

  // Focus the trigger on mount when requested (head row just entered edit mode).
  useEffect(() => {
    if (autoFocus) triggerRef.current?.focus();
  }, [autoFocus]);

  // Reset the active row whenever the popup opens or the filter changes.
  useEffect(() => {
    if (open) setActive(0);
  }, [open, query]);

  // Searchable: focus the embedded search input when the popup opens.
  useEffect(() => {
    if (open && searchable) searchRef.current?.focus();
  }, [open, searchable]);

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
    setQuery('');
    const idx = searchable ? 0 : Math.max(0, ['', ...options].indexOf(value));
    setActive(idx);
    setOpen(true);
  }, [searchable, options, value]);

  // Commit a value and return focus to the trigger.
  const commit = useCallback(
    (v: string) => {
      onChange(v);
      setOpen(false);
      triggerRef.current?.focus();
    },
    [onChange],
  );

  const commitActive = useCallback(() => {
    const v = entries[active];
    if (v === undefined) {
      setOpen(false);
      return;
    }
    onChange(v.trim());
    setOpen(false);
  }, [entries, active, onChange]);

  // Shared nav for both the trigger (ENUM) and the search input (SEARCHABLE).
  const handleNavKey = (e: KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActive((p) => (entries.length ? (p + 1) % entries.length : 0));
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActive((p) => (entries.length ? (p - 1 + entries.length) % entries.length : 0));
        break;
      case 'Enter':
        e.preventDefault();
        commitActive();
        triggerRef.current?.focus();
        break;
      case 'Escape':
        e.preventDefault();
        setOpen(false);
        break;
      case 'Tab':
        e.preventDefault();
        commitActive();
        onTabNext?.();
        break;
      default:
        break;
    }
  };

  const handleTriggerKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    if (searchable) return; // the search input owns keys while open
    if (e.key === ' ') {
      e.preventDefault();
      commitActive();
      triggerRef.current?.focus();
      return;
    }
    handleNavKey(e);
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
        onKeyDown={handleTriggerKeyDown}
      >
        {value || placeholder}
      </button>
      {open && (
        <div className="mh-heading-pop" role="listbox" aria-label={ariaLabel}>
          {searchable && (
            <div className="mh-heading-search">
              <input
                ref={searchRef}
                type="text"
                value={query}
                placeholder={t('editor.locationSearchPlaceholder')}
                aria-label={t('editor.locationSearchPlaceholder')}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={handleNavKey}
              />
            </div>
          )}
          {tabHint && (
            <div className="mh-heading-hint" aria-hidden="true">
              <kbd className="mh-pop-kbd">Tab</kbd>
              <span>{tabHint}</span>
            </div>
          )}
          {searchable && entries.length === 0 && (
            <div className="mh-heading-empty">{t('editor.locationNoMatch')}</div>
          )}
          {entries.map((opt, i) => (
            <div
              key={`${opt || '__unset__'}-${i}`}
              role="option"
              aria-selected={opt === value}
              className={`mh-heading-opt${i === active ? ' active' : ''}${
                i === createIndex ? ' create' : ''
              }`}
              // preventDefault so clicking never blurs the head row (which would
              // otherwise collapse it back to read mode before the commit lands).
              onMouseDown={(e) => {
                e.preventDefault();
                commit(opt.trim());
              }}
              onMouseEnter={() => setActive(i)}
            >
              {i === createIndex
                ? t('editor.locationCreate', { name: opt.trim() })
                : opt || '—'}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
