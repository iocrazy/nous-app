// features/canvas-core/library/libraryChrome.ts
//
// The two scraps the panel and its Media page both need. Kept out of either
// component so importing a chip style does not drag a page's worth of hooks
// along with it.

/** A label as `[i18n key, English default]`. The keys are single-quoted
 *  literals at every use site on purpose: `libraryI18n.test.ts` scans for
 *  exactly that shape, and a key assembled by template literal is invisible
 *  to it — the guard would report fewer keys than the UI asks for and pass. */
export type Label = readonly [key: string, english: string];

const CHIP_BASE =
  'nodrag shrink-0 rounded-full border px-2 py-0.5 text-[11px] transition-colors';
const CHIP_ON = 'border-[var(--accent-border)] text-[var(--accent-text)]';
const CHIP_OFF = 'border-canvas-line text-canvas-muted hover:text-canvas-text';

export function chipClass(on: boolean): string {
  return `${CHIP_BASE} ${on ? CHIP_ON : CHIP_OFF}`;
}

/** An ACTION, not a filter. Squared rather than round, filled rather than
 *  flat, and it expects a leading icon — the round flat pills above it narrow
 *  what is on screen, these two write to the shelf, and a user should not have
 *  to read the label to tell those apart.
 *
 *  `border-canvas-line` is the load-bearing token: the panel's whole rule is
 *  "if it is clickable it carries a visible border". The two prompt-page
 *  actions were the only exceptions (`border-transparent`) and duly got
 *  reported as not looking like buttons.
 *
 *  Hover is `enabled:` — a `hover:` alone still lights up a disabled button,
 *  and `Save current…` is disabled whenever no prompt node is aimed at. */
export const ACTION_BTN =
  'nodrag inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border border-canvas-line bg-canvas-card/60 px-2 py-0.5 text-[11px] font-medium text-canvas-text transition-colors enabled:hover:border-[var(--accent-border)] enabled:hover:text-[var(--accent-text)] disabled:cursor-not-allowed disabled:opacity-40';
