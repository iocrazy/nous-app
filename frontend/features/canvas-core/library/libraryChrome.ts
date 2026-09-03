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
