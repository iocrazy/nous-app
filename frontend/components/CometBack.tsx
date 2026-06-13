import React from 'react';
import { ArrowLeft } from 'lucide-react';

interface CometBackProps {
  onClick: () => void;
  /** Accessible label (defaults to "Back"). */
  title?: string;
}

/**
 * D10 "comet" back button — a circular violet ring; the 2px fading light trail
 * that sweeps under the title on hover is drawn by the parent `.stage-head`
 * (see `.comet-trail` in index.css), so render this as the first child of a
 * `.stage-head` and put a <span className="comet-trail" /> right after the title.
 */
export const CometBack: React.FC<CometBackProps> = ({ onClick, title = 'Back' }) => (
  <button type="button" onClick={onClick} aria-label={title} title={title} className="comet-back">
    <ArrowLeft size={16} />
  </button>
);
