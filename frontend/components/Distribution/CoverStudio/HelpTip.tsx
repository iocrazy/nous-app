// components/Distribution/CoverStudio/HelpTip.tsx
//
// A "?" that carries the explanatory sentence instead of the card carrying a
// paragraph. The bubble is our own (help-tip.css) and appears the moment the
// pointer lands — the native `title` tooltip waits about a second, which
// read as "nothing happens" next to the platform's instant one.

import React from 'react';
import { HelpCircle } from 'lucide-react';

import './help-tip.css';

export function HelpTip({ text }: { text: string }): React.JSX.Element {
  return (
    <span className="cs-help" aria-label={text} role="img" tabIndex={0} data-testid="cs-help">
      <HelpCircle size={13} />
      {/* The sentence lives in an attribute and is painted by CSS, so it is
          never part of the heading's text — tests and screen readers read
          the heading, the aria-label carries the help. */}
      <span className="cs-tip" data-tip={text} aria-hidden="true" />
    </span>
  );
}
