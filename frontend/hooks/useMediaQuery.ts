import { useEffect, useState } from 'react';

function hasMatchMedia(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function';
}

/**
 * Live result of a CSS media query. SPA-only (no SSR). Where `matchMedia` is
 * unavailable (jsdom, very old browsers) it returns `fallback`.
 */
export function useMediaQuery(query: string, fallback: boolean): boolean {
  const [matches, setMatches] = useState<boolean>(() =>
    hasMatchMedia() ? window.matchMedia(query).matches : fallback,
  );
  useEffect(() => {
    if (!hasMatchMedia()) return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener?.('change', onChange);
    return () => mql.removeEventListener?.('change', onChange);
  }, [query]);
  return matches;
}
