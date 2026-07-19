/**
 * MentionDropdown — candidate list that floats above the Composer.
 *
 * The parent element must have `position: relative` so this component's
 * `absolute bottom-full` positioning resolves correctly.
 *
 * Renders null when `open` is false or `items` is empty.
 */

import React from 'react';

// ── Types ────────────────────────────────────────────────────────────────────

export type MentionCandidate =
  | { kind: 'user'; id: string; label: string }
  | { kind: 'agent'; slug: string; label: string };

// ── Component ────────────────────────────────────────────────────────────────

interface Props {
  open: boolean;
  items: MentionCandidate[];
  activeIndex: number;
  onPick: (c: MentionCandidate) => void;
}

function _initial(label: string): string {
  return (label.trim()[0] ?? '?').toUpperCase();
}

function _key(c: MentionCandidate): string {
  return c.kind === 'user' ? `user:${c.id}` : `agent:${c.slug}`;
}

export function MentionDropdown({
  open,
  items,
  activeIndex,
  onPick,
}: Props): React.ReactElement | null {
  if (!open || items.length === 0) return null;

  return (
    <div className="absolute bottom-full left-0 right-0 mb-[6px] z-50">
      <div className="bg-card border border-line-strong rounded-[10px] shadow-2xl overflow-hidden">
        {items.map((c, idx) => {
          const active = idx === activeIndex;
          return (
            <button
              key={_key(c)}
              type="button"
              onMouseDown={(e) => {
                // Prevent textarea blur before the click registers
                e.preventDefault();
                onPick(c);
              }}
              className={[
                'w-full text-left flex items-center gap-[10px] px-3 py-[7px]',
                'transition-colors',
                active
                  ? 'bg-[var(--accent-soft)]'
                  : 'hover:bg-white/[.04]',
              ].join(' ')}
            >
              {c.kind === 'user' ? (
                <span
                  className="w-[22px] h-[22px] flex-shrink-0 rounded-[6px] grid place-items-center text-[10px] font-semibold bg-gradient-to-br from-slate-500 to-slate-400 text-white"
                  aria-hidden="true"
                >
                  {_initial(c.label)}
                </span>
              ) : (
                <span className="text-[10px] font-semibold text-amber-400 bg-amber-400/10 px-[6px] py-[1.5px] rounded-[5px] tracking-[0.02em] flex-shrink-0">
                  AGENT
                </span>
              )}
              <span className="text-[13px] text-content truncate">{c.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
