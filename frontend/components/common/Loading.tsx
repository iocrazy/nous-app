/**
 * Loading — the one shared loading indicator (neutral-ink language, matching
 * the #1438 editor chrome). A three-dot pulse in `currentColor`, so it inherits
 * whatever ink the surrounding context sets: the app's `text-ink-*` palette AND
 * the editor shell's scoped `--ink` theme both resolve through `bg-current`, and
 * light/dark come for free with the inherited colour.
 *
 * Two sizes of footprint:
 *   • inline (default) — sits in a row of text / beside a label;
 *   • center — fills its box and centres (drop-in for a whole loading region).
 *
 * Pass `label` to show text after the dots (already localised by the caller).
 */
import type { CSSProperties } from 'react';

export interface LoadingProps {
  /** Optional caption shown after the dots (caller localises it). */
  label?: string;
  /** Dot scale. 'sm' for inline captions, 'md' (default) for regions. */
  size?: 'sm' | 'md';
  /** Fill + centre inside the parent box (whole-region loading state). */
  center?: boolean;
  className?: string;
  'data-testid'?: string;
}

const DOT_SIZE: Record<'sm' | 'md', string> = {
  sm: 'w-1 h-1',
  md: 'w-1.5 h-1.5',
};

export function Loading({
  label,
  size = 'md',
  center = false,
  className = '',
  'data-testid': testId = 'loading',
}: LoadingProps) {
  const wrap = center
    ? 'flex items-center justify-center gap-2 w-full h-full min-h-[2.5rem]'
    : 'inline-flex items-center gap-2';
  return (
    <div
      className={`${wrap} ${className}`.trim()}
      role="status"
      aria-live="polite"
      aria-busy="true"
      data-testid={testId}
    >
      <span className="inline-flex items-center gap-1" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className={`${DOT_SIZE[size]} rounded-full bg-current animate-pulse`}
            // Stagger the pulse so the three dots read as a travelling wave
            // rather than blinking in unison. A negative delay starts each dot
            // mid-cycle so the animation is already in motion on first paint.
            style={{ animationDelay: `${i * 160 - 320}ms` } as CSSProperties}
          />
        ))}
      </span>
      {label && <span className="text-[13px] leading-none">{label}</span>}
    </div>
  );
}

export default Loading;
