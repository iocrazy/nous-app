/**
 * SceneHeadingSelect — the INT/EXT and time-of-day pickers in the scene heading
 * row. Reuses the editor's `.mh-mention-pop` popup DNA (the same clean surface
 * the character-cue and transition pickers ride) so EVERY dropdown inside the
 * script editor shares one laper-parity look, instead of the app-wide `UiSelect`
 * whose portal menu rendered with the wrong (main-app) tokens on the paper sheet.
 *
 * Small fixed option sets (~5-7), so no search field — just the trigger pill +
 * an in-editor popup with keyboard nav, click-outside / Esc close.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

export interface SceneHeadingSelectProps {
  value: string;
  /** Concrete choices; the leading `—` (clear) row is added automatically. */
  options: string[];
  onChange: (value: string) => void;
  ariaLabel: string;
  /** Placeholder shown on the trigger when the value is empty. */
  placeholder?: string;
  /** Focus the trigger on mount (heading enters edit mode → land on INT/EXT). */
  autoFocus?: boolean;
}

const CLEAR = '';

export function SceneHeadingSelect({
  value,
  options,
  onChange,
  ariaLabel,
  placeholder = '—',
  autoFocus = false,
}: SceneHeadingSelectProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listboxId = useId();

  // `—` (clear) first, then the real options.
  const rows = useMemo(() => [CLEAR, ...options], [options]);

  useEffect(() => {
    if (autoFocus) triggerRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const commit = (next: string) => {
    onChange(next);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const step = (dir: 1 | -1) => {
    const idx = rows.indexOf(value);
    const start = idx === -1 ? 0 : idx;
    const next = (start + dir + rows.length) % rows.length;
    onChange(rows[next]);
  };

  return (
    <div ref={wrapRef} className="mh-scene-select-wrap">
      <button
        ref={triggerRef}
        type="button"
        className="mh-scene-select"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-label={ariaLabel}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (!open) setOpen(true);
            else step(e.key === 'ArrowDown' ? 1 : -1);
          } else if ((e.key === 'Enter' || e.key === ' ') && !open) {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span className="mh-scene-select-value">{value || placeholder}</span>
        <ChevronDown size={11} className="mh-scene-select-chev" aria-hidden="true" />
      </button>
      {open && (
        <div className="mh-mention-pop mh-scene-select-pop" role="listbox" id={listboxId} aria-label={ariaLabel}>
          <ul className="mh-mention-list">
            {rows.map((opt) => {
              const selected = opt === value;
              return (
                <li
                  key={opt || '__clear__'}
                  role="option"
                  aria-selected={selected}
                  className={`mh-mention-opt${selected ? ' active' : ''}`}
                  onMouseDown={(e) => {
                    // mousedown (not click) so the trigger's blur doesn't beat us.
                    e.preventDefault();
                    commit(opt);
                  }}
                >
                  {opt || placeholder}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

export default SceneHeadingSelect;
