// components/Distribution/CoverStudio/HelpTip.tsx
//
// A "?" that carries the explanatory sentence instead of the card carrying a
// paragraph. The bubble is our own (help-tip.css) and appears the moment the
// pointer lands — the native `title` tooltip waits about a second, which
// read as "nothing happens" next to the platform's instant one.
//
// The bubble anchors itself to whichever side has room: centred by default,
// growing rightward when the (?) sits near the left edge of the viewport and
// leftward near the right edge — a centred bubble on a left-edge (?) used to
// hang half outside the card.

import React, { useState } from 'react';
import { HelpCircle } from 'lucide-react';

import './help-tip.css';

type Side = 'center' | 'left' | 'right';
const EDGE = 170; // half the bubble's max width, plus a little

export function HelpTip({ text }: { text: string }): React.JSX.Element {
  const [side, setSide] = useState<Side>('center');
  const place = (el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    if (r.left < EDGE) setSide('left');
    else if (vw - r.right < EDGE) setSide('right');
    else setSide('center');
  };
  return (
    <span
      className={`cs-help ${side !== 'center' ? `from-${side}` : ''}`}
      aria-label={text}
      role="img"
      tabIndex={0}
      data-testid="cs-help"
      onMouseEnter={(e) => place(e.currentTarget)}
      onFocus={(e) => place(e.currentTarget)}
    >
      <HelpCircle size={13} />
      {/* The sentence lives in an attribute and is painted by CSS, so it is
          never part of the heading's text — tests and screen readers read
          the heading, the aria-label carries the help. */}
      <span className="cs-tip" data-tip={text} aria-hidden="true" />
    </span>
  );
}
