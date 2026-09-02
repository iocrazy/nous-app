// frontend/components/resources/SelectionCheck.tsx
//
// The circular multi-select check that sits on a card's thumbnail.
//
// Extracted so the Generated inbox and My Uploads cannot look like two
// different products. The geometry, position and reveal behaviour are lifted
// VERBATIM from `ResourceCard`'s inline control (`absolute top-2 left-2`,
// `w-6 h-6 rounded-full`, a 12px `Check`, hidden until hover unless checked or
// forced), because "the same as My Uploads" is the requirement — a
// re-implementation that merely looked similar is what produced the square,
// top-RIGHT box this replaces.
//
// Two deliberate departures from that inline copy:
//
//  * `bg-accent` / `border-line-strong` instead of `bg-indigo-500` /
//    `border-ink-400`. The indigo ramp was re-hued to green in K1, so those
//    names no longer say what they render; new code uses the semantic tokens
//    (CLAUDE.md 开发规范). Same green, one step darker.
//  * A real accessible name. The inline control is a bare `<button>` with an
//    icon and no label — reachable by tab, unreadable by a screen reader and
//    unfindable by `getByRole('checkbox', { name })`. Here it is a checkbox
//    with `aria-checked` and a caller-supplied label.
//
// `ResourceCard` still carries its own copy; adopting this there is a
// follow-up, kept out of this change because that file is being edited on
// another branch.

import React from 'react';
import { Check } from 'lucide-react';

export interface SelectionCheckProps {
  checked: boolean;
  /** Accessible name — include what is being selected, not just "Select". */
  label: string;
  onToggle: () => void;
  /** Keep it visible even when unchecked and not hovered (bulk-select mode). */
  forceVisible?: boolean;
  className?: string;
}

export const SelectionCheck: React.FC<SelectionCheckProps> = ({
  checked,
  label,
  onToggle,
  forceVisible = false,
  className = '',
}) => (
  <div
    className={`absolute top-2 left-2 z-10 transition-opacity ${
      forceVisible || checked ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'
    } ${className}`}
  >
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      data-testid="selection-check"
      onClick={(e) => {
        // The thumbnail underneath opens a lightbox — without this, picking
        // a card for a batch action would also open the viewer over it.
        e.stopPropagation();
        onToggle();
      }}
      className={`flex h-6 w-6 items-center justify-center rounded-full transition-colors ${
        checked
          ? 'bg-accent text-white shadow-lg'
          : 'border border-line-strong bg-black/50 text-transparent hover:text-white/70'
      }`}
    >
      <Check size={12} aria-hidden="true" />
    </button>
  </div>
);
