import { useMediaQuery } from '../../hooks/useMediaQuery';

/** Live `prefers-reduced-motion`. False where matchMedia is unavailable (jsdom). */
export function usePrefersReducedMotion(): boolean {
  return useMediaQuery('(prefers-reduced-motion: reduce)', false);
}
