import { useMediaQuery } from './useMediaQuery';

/** True at >= Tailwind `sm` (640px). SPA-only (no SSR). Used to render the
 *  desktop island frame vs the classic mobile main — island UI is desktop-only. */
export function useIsDesktop(): boolean {
  return useMediaQuery('(min-width: 640px)', true);
}
