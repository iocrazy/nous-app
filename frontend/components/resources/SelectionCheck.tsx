// frontend/components/resources/SelectionCheck.tsx
//
// The circular multi-select check that sits on a card's thumbnail.
//
// Extracted so the Generated inbox and My Uploads stop looking like two
// different products.
//
// WHAT IS LIFTED VERBATIM from `ResourceCard`'s inline control: the geometry
// and position (`absolute top-2 left-2 z-10`, `h-6 w-6 rounded-full`, a 12px
// `Check`) and the reveal rule (transparent until hover unless checked or
// forced). Those are what the eye reads, and a re-implementation that merely
// looked similar is what produced the square, top-RIGHT box this replaces.
//
// WHAT IS NOT IDENTICAL — three departures, so nobody reads this as pixel
// parity:
//
//  * Checked fill. `bg-accent` (#1E7A5B) rather than `bg-indigo-500`
//    (#4D9478). Same hue, one step darker. The indigo ramp was re-hued to
//    green in K1, so those class names no longer say what they render and new
//    code uses the semantic tokens (CLAUDE.md 开发规范).
//  * Unchecked hover. Here the tick fades in (`hover:text-white/70`); there
//    the ring lightens (`hover:border-ink-200`).
//  * A real accessible name. The inline control is a bare `<button>` with an
//    icon and no label — reachable by tab, unreadable by a screen reader and
//    unfindable by `getByRole('checkbox', { name })`. Here it is a checkbox
//    with `aria-checked` and a caller-supplied label. Copying that would have
//    been copying a defect.
//
// So the two surfaces render slightly different greens side by side until
// `ResourceCard` / `CompactMediaCard` / `FolderCard` adopt this. That swap is
// the follow-up; it is out of this change because those files are being
// edited on `feat/uploads-adaptive-grid` and a cross-branch collision on a
// shipped surface is not worth the tidiness.

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
