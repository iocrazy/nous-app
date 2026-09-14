// frontend/components/ViewModeMenu.tsx
//
// One control for "which way should this list be laid out". It names the mode
// you are in and offers the others; it does not make you guess.
//
// It replaces two shapes of the same problem:
//
//  * My Downloads rendered FOUR buttons side by side — four unlabelled glyphs,
//    no grouping role, no pressed state, and three of them always the wrong
//    answer. It also cost four toolbar slots on a row that has to hold search,
//    filters and sort.
//  * My Uploads rendered ONE button that CYCLED grid → justified → list. That
//    was deliberate, not broken, but a cycle never says what mode you are in
//    or how many there are, so finding a view means clicking past it.
//
// THE MODE LIST IS A PROP, and that is not generality for its own sake: the
// two surfaces really do differ. Downloads has a `feed` view (which also
// changes scroll-memory and portal behaviour, not just layout) and Uploads has
// no renderer for it; their preferences live under two separate storage keys
// with their own migration history. A list hard-coded here would have grown a
// feed option on a page that cannot draw one.
//
// `role="menu"` announces a keyboard contract — arrows move, Escape closes and
// returns you where you were — and declaring it without implementing it is
// worse than a plain popover, which promises nothing. The model below is the
// one `AssetCardMenu` established in this repo; the items are
// `menuitemradio` rather than `menuitem` because this is a single choice out
// of a known set, and `aria-checked` is what tells a screen-reader user which
// one they are on.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronDown } from 'lucide-react';

export interface ViewModeOption<T extends string = string> {
  value: T;
  /** A lucide icon component. */
  icon: React.ComponentType<{ size?: number; className?: string }>;
  /** Already translated by the caller — the two call sites own their keys. */
  label: string;
}

export interface ViewModeMenuProps<T extends string = string> {
  modes: ReadonlyArray<ViewModeOption<T>>;
  value: T;
  onChange: (next: T) => void;
  /** Extra classes for the trigger, so a toolbar can match its neighbours. */
  className?: string;
}

export function ViewModeMenu<T extends string = string>({
  modes,
  value,
  onChange,
  className = '',
}: ViewModeMenuProps<T>): React.ReactElement {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // A stored preference can name a mode this surface does not offer (separate
  // keys, separate sets). Falling back to the first option keeps the trigger
  // drawable instead of crashing on `current.icon`; nothing is marked checked,
  // which is the honest report — you are not in any of these.
  const current = useMemo(
    () => modes.find((m) => m.value === value) ?? modes[0],
    [modes, value],
  );

  const items = useCallback(
    (): HTMLElement[] =>
      Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? []),
    [],
  );

  /** Close and put the caret back on the trigger. Every close path routes
   *  through here except the outside CLICK, where the user has already moved
   *  their own focus and yanking it back would fight them. */
  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  // Opening moves focus INTO the menu: a menu you have to tab into is a menu
  // the keyboard user cannot tell opened.
  useEffect(() => {
    if (!open) return;
    items()[0]?.focus();
  }, [open, items]);

  const onMenuKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const list = items();
      if (list.length === 0) return;
      const at = list.indexOf(document.activeElement as HTMLElement);
      if (e.key === 'Escape') {
        e.preventDefault();
        close();
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        list[(at + 1 + list.length) % list.length]?.focus();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        list[(at - 1 + list.length) % list.length]?.focus();
      } else if (e.key === 'Home') {
        e.preventDefault();
        list[0]?.focus();
      } else if (e.key === 'End') {
        e.preventDefault();
        list[list.length - 1]?.focus();
      } else if (e.key === 'Tab') {
        // Tabbing away is a close, not a trap — but focus goes where the user
        // aimed it, so no restore.
        close(false);
      }
    },
    [items, close],
  );

  const onTriggerKeyDown = useCallback((e: React.KeyboardEvent<HTMLButtonElement>) => {
    // The standard way into a menu button. Enter / Space already open it via
    // the native button click.
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      setOpen(true);
    }
  }, []);

  const choose = useCallback(
    (next: T) => {
      close();
      // Re-picking the mode you are already in is a close, not a change: the
      // preference write and the re-render it triggers would both be noise.
      if (next !== value) onChange(next);
    },
    [close, onChange, value],
  );

  const CurrentIcon = current.icon;

  return (
    /* `inline-flex`, not a bare block: the menu is positioned with `top-full`
       against this element, so anything that stretches it puts the menu that
       far down the page. A flex parent with the default `align-items:
       stretch` does exactly that — measured at 1145px tall in one such host —
       and the fix belongs here rather than in every toolbar that mounts it. */
    <div ref={rootRef} className="relative inline-flex shrink-0">
      <button
        ref={triggerRef}
        type="button"
        data-testid="view-mode-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('resources.viewMode', {
          mode: current.label,
          defaultValue: 'View: {{mode}}',
        })}
        title={current.label}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKeyDown}
        className={`flex items-center gap-0.5 rounded-lg p-1.5 text-ink-400 transition-colors hover:bg-ink-800 hover:text-ink-200 ${className}`}
      >
        <CurrentIcon size={14} />
        <ChevronDown size={10} aria-hidden="true" className="opacity-60" />
      </button>

      {open && (
        <div
          ref={menuRef}
          role="menu"
          data-testid="view-mode-menu"
          onKeyDown={onMenuKeyDown}
          className="absolute right-0 top-full z-30 mt-1 min-w-[10rem] overflow-hidden rounded-xl border border-line bg-card py-1 shadow-2xl"
        >
          {modes.map((mode) => {
            const Icon = mode.icon;
            const active = mode.value === value;
            return (
              <button
                key={mode.value}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                data-mode={mode.value}
                onClick={() => choose(mode.value)}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-island-2 ${
                  active ? 'text-[var(--accent-text)]' : 'text-content-2'
                }`}
              >
                <Icon size={13} aria-hidden="true" className="shrink-0 opacity-80" />
                <span className="flex-1 truncate">{mode.label}</span>
                {active && <Check size={12} aria-hidden="true" className="shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
