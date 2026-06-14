import { useState, useEffect } from 'react';

/** True at >= Tailwind `sm` (640px). SPA-only (no SSR). Used to render the
 *  desktop island frame vs the classic mobile main — island UI is desktop-only. */
export function useIsDesktop(): boolean {
  const query = '(min-width: 640px)';
  const [isDesktop, setIsDesktop] = useState<boolean>(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : true,
  );
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mql = window.matchMedia(query);
    const onChange = () => setIsDesktop(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);
  return isDesktop;
}
