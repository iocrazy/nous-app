// components/Distribution/CoverStudio/HelpTip.tsx
//
// A "?" that carries the explanatory sentence instead of the card carrying a
// paragraph. The studio had grown a paragraph under almost every card; the
// user asked for the words to move out of the way. Native `title` is the
// tooltip — no library, keyboard-reachable, screen-reader readable via
// aria-label.

import React from 'react';
import { HelpCircle } from 'lucide-react';

export function HelpTip({ text }: { text: string }): React.JSX.Element {
  return (
    <span className="cs-help" title={text} aria-label={text} role="img" tabIndex={0} data-testid="cs-help">
      <HelpCircle size={13} />
    </span>
  );
}
