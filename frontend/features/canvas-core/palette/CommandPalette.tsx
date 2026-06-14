/**
 * Cmd+K command palette (Phase 6c).
 *
 * Focus-trapped modal: a search input narrows the command list; ↑↓ navigate;
 * Enter runs the highlighted command; Esc closes. The search <input> holds
 * focus the whole time, so `isInsideEditable` in `useCanvasShortcuts`
 * naturally ignores all keystrokes that originate here.
 *
 * Mount once in CanvasPage and toggle via the `open` prop.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import { buildCanvasCommands, filterCommands, type Command } from './commands';

// ---- Props ---------------------------------------------------------------

interface CommandPaletteProps {
  open: boolean;
  onClose(): void;
  /**
   * Override the command list — used in tests to avoid store coupling.
   * Defaults to `buildCanvasCommands()` when omitted.
   */
  commands?: Command[];
}

// ---- Component -----------------------------------------------------------

export function CommandPalette({
  open,
  onClose,
  commands: commandsProp,
}: CommandPaletteProps) {
  const [query, setQuery] = useState('');
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Build the command list once per mount (enabled() closures are lazy, so
  // live store data is always read at render time — no need to rebuild).
  const allCommands = useMemo(
    () => commandsProp ?? buildCanvasCommands(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [commandsProp],
  );

  const filtered = useMemo(
    () => filterCommands(query, allCommands),
    [query, allCommands],
  );

  // On open: focus input + reset state.
  useEffect(() => {
    if (open) {
      setQuery('');
      setHighlightedIndex(0);
      inputRef.current?.focus();
    }
  }, [open]);

  if (!open) return null;

  // ---- Helpers -----------------------------------------------------------

  function runAt(index: number): void {
    const cmd = filtered[index];
    if (!cmd) return;
    if (cmd.enabled && !cmd.enabled()) return;
    cmd.run();
    onClose();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    switch (e.key) {
      case 'Escape':
        e.preventDefault();
        onClose();
        break;

      case 'ArrowDown':
        e.preventDefault();
        setHighlightedIndex((prev) =>
          filtered.length === 0 ? 0 : (prev + 1) % filtered.length,
        );
        break;

      case 'ArrowUp':
        e.preventDefault();
        setHighlightedIndex((prev) =>
          filtered.length === 0
            ? 0
            : (prev - 1 + filtered.length) % filtered.length,
        );
        break;

      case 'Enter':
        e.preventDefault();
        runAt(highlightedIndex);
        break;

      case 'Tab':
        // Only one focusable element in this panel — prevent focus escape.
        e.preventDefault();
        break;
    }
  }

  function handleQueryChange(e: React.ChangeEvent<HTMLInputElement>): void {
    setQuery(e.target.value);
    setHighlightedIndex(0); // reset highlight on every keystroke
  }

  // ---- Render ------------------------------------------------------------

  return (
    // Backdrop — clicking outside the panel closes the palette.
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]"
      onClick={onClose}
    >
      {/* Dimmed background */}
      <div className="absolute inset-0 bg-black/50" />

      {/* Panel — stop propagation so clicks inside don't close the palette. */}
      <div
        className="relative w-full max-w-lg rounded-xl border border-ink-700/40 bg-ink-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search row */}
        <div className="border-b border-ink-700/40 px-4 py-3">
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-autocomplete="list"
            aria-expanded={true}
            aria-controls="palette-list"
            aria-activedescendant={
              filtered[highlightedIndex]
                ? `palette-item-${filtered[highlightedIndex].id}`
                : undefined
            }
            value={query}
            onChange={handleQueryChange}
            onKeyDown={handleKeyDown}
            placeholder="Search commands…"
            className="w-full bg-transparent text-sm text-ink-100 placeholder:text-ink-600 focus:outline-none"
          />
        </div>

        {/* Command list */}
        <ul
          id="palette-list"
          role="listbox"
          className="max-h-72 overflow-y-auto py-2"
        >
          {filtered.length === 0 ? (
            <li className="px-4 py-2 text-sm text-ink-500">No commands found</li>
          ) : (
            filtered.map((cmd, index) => {
              const isHighlighted = index === highlightedIndex;
              const isDisabled = cmd.enabled ? !cmd.enabled() : false;

              return (
                <li
                  key={cmd.id}
                  id={`palette-item-${cmd.id}`}
                  role="option"
                  aria-selected={isHighlighted}
                  aria-disabled={isDisabled}
                  onMouseEnter={() => setHighlightedIndex(index)}
                  onClick={() => {
                    if (isDisabled) return;
                    runAt(index);
                  }}
                  className={[
                    'flex cursor-pointer select-none items-center justify-between px-4 py-2 text-sm transition-colors',
                    isHighlighted
                      ? 'bg-ink-800 text-ink-100'
                      : 'text-ink-300 hover:bg-ink-800/60',
                    isDisabled ? 'cursor-default opacity-40' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                >
                  <span>{cmd.title}</span>
                  {cmd.hint !== undefined && (
                    <span className="ml-4 shrink-0 text-xs text-ink-600">
                      {cmd.hint}
                    </span>
                  )}
                </li>
              );
            })
          )}
        </ul>
      </div>
    </div>
  );
}
