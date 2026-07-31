/**
 * BackToTopButton — floating "scroll back to the top" affordance for the
 * My Downloads list.
 *
 * Watches BOTH scroll surfaces because which one moves depends on the
 * breakpoint: on desktop the content container scrolls
 * (``md:overflow-y-auto``), on mobile the document does. The button appears
 * once either passes ``threshold``.
 *
 * Kept mounted (rather than conditionally rendered) so the fade/slide
 * transition plays in both directions; it is taken out of the a11y tree and
 * the tab order while hidden.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { ArrowUp } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface BackToTopButtonProps {
  /** The container that scrolls on desktop. */
  scrollerRef: React.RefObject<HTMLElement | null>;
  /** Offset (px) past which the button shows. */
  threshold?: number;
}

export const BackToTopButton: React.FC<BackToTopButtonProps> = ({
  scrollerRef,
  threshold = 600,
}) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let rafId: number | null = null;
    const measure = () => {
      rafId = null;
      const containerTop = scrollerRef.current?.scrollTop ?? 0;
      const documentTop = typeof window === 'undefined' ? 0 : window.scrollY || 0;
      setVisible(Math.max(containerTop, documentTop) > threshold);
    };
    const onScroll = () => {
      if (rafId != null) return; // coalesce bursts into one measurement
      rafId = requestAnimationFrame(measure);
    };

    // Measure once on mount: a restored scroll offset is applied
    // programmatically and we must not wait for a user gesture to catch up.
    measure();

    const scroller = scrollerRef.current;
    scroller?.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      if (rafId != null) cancelAnimationFrame(rafId);
      scroller?.removeEventListener('scroll', onScroll);
      window.removeEventListener('scroll', onScroll);
    };
  }, [scrollerRef, threshold]);

  const handleClick = useCallback(() => {
    const scroller = scrollerRef.current;
    // Element.scrollTo is absent in jsdom and older WebViews — fall back to
    // assigning scrollTop, which every engine supports (just without easing).
    if (scroller) {
      if (typeof scroller.scrollTo === 'function') {
        scroller.scrollTo({ top: 0, behavior: 'smooth' });
      } else {
        scroller.scrollTop = 0;
      }
    }
    if (typeof window !== 'undefined' && typeof window.scrollTo === 'function') {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }, [scrollerRef]);

  const label = t('common.backToTop', 'Back to top');

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-hidden={!visible}
      aria-label={label}
      title={label}
      tabIndex={visible ? 0 : -1}
      data-testid="downloads-back-to-top"
      // Right rail, clear of the centered Load More pill and — on desktop —
      // stacked above the chat FAB (which sits at bottom-20, 48 px tall).
      className={`fixed right-4 z-30 flex h-10 w-10 items-center justify-center rounded-full
        border border-ink-700/60 bg-ink-900/95 text-ink-200 shadow-2xl backdrop-blur-md
        transition-all duration-200 hover:bg-ink-800 hover:text-white
        bottom-[calc(5.5rem+env(safe-area-inset-bottom,6px))] md:bottom-36 ${
          visible
            ? 'translate-y-0 opacity-100'
            : 'pointer-events-none translate-y-2 opacity-0'
        }`}
    >
      <ArrowUp size={18} />
    </button>
  );
};
