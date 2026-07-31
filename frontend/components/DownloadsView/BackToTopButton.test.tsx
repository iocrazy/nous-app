import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? _k }),
}));

import { BackToTopButton } from './BackToTopButton';

/** jsdom never lays out, so drive scrollTop by hand and fire the event. */
function scrollContainerTo(el: HTMLElement, top: number) {
  el.scrollTop = top;
  act(() => {
    el.dispatchEvent(new Event('scroll'));
    vi.advanceTimersByTime(32); // flush the rAF coalescing
  });
}

function Harness({ threshold }: { threshold?: number }) {
  const ref = React.useRef<HTMLDivElement>(null);
  return (
    <div>
      <div ref={ref} data-testid="scroller" style={{ overflowY: 'auto' }} />
      <BackToTopButton scrollerRef={ref} threshold={threshold} />
    </div>
  );
}

describe('BackToTopButton', () => {
  it('stays hidden until the scroller passes the threshold', () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
    try {
      render(<Harness threshold={600} />);
      const button = screen.getByTestId('downloads-back-to-top');
      const scroller = screen.getByTestId('scroller');

      expect(button).toHaveAttribute('aria-hidden', 'true');

      scrollContainerTo(scroller, 400);
      expect(button).toHaveAttribute('aria-hidden', 'true');

      scrollContainerTo(scroller, 601);
      expect(button).toHaveAttribute('aria-hidden', 'false');

      // …and hides again on the way back up.
      scrollContainerTo(scroller, 0);
      expect(button).toHaveAttribute('aria-hidden', 'true');
    } finally {
      vi.useRealTimers();
    }
  });

  it('appears on mount when the offset was already restored', () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
    try {
      vi.spyOn(window, 'scrollY', 'get').mockReturnValue(1500);
      render(<Harness />);
      expect(screen.getByTestId('downloads-back-to-top')).toHaveAttribute('aria-hidden', 'false');
    } finally {
      vi.useRealTimers();
    }
  });

  it('scrolls both surfaces to the top when clicked', () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
    try {
      const windowScrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
      render(<Harness threshold={600} />);
      const scroller = screen.getByTestId('scroller');
      const elementScrollTo = vi.fn();
      (scroller as any).scrollTo = elementScrollTo;

      scrollContainerTo(scroller, 900);
      const button = screen.getByTestId('downloads-back-to-top');
      expect(button).toHaveAttribute('aria-hidden', 'false');

      act(() => {
        button.click();
      });

      expect(elementScrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' });
      expect(windowScrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' });
    } finally {
      vi.useRealTimers();
    }
  });

  it('falls back to scrollTop when the engine lacks Element.scrollTo', () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
    try {
      vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
      render(<Harness threshold={600} />);
      const scroller = screen.getByTestId('scroller');
      scrollContainerTo(scroller, 900);

      act(() => {
        screen.getByTestId('downloads-back-to-top').click();
      });

      expect(scroller.scrollTop).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
