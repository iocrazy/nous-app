// The candidate grid for an @-mention over a node's input images.
//
// Selection is bound to mousedown with preventDefault, not click: the editor
// must keep focus, or its blur closes the popover before a click resolves.
//
// `nodrag`/`nowheel` are not decoration. React Flow listens for mousedown on
// the node and stops propagation, so without them React's synthetic events
// never fire in here and every row silently does nothing for a real user —
// while a synthetic dispatch in a test still "works", which is exactly how
// such a bug reaches production.

import React from 'react';

import { mediaSrc } from '../mediaUrl';
import type { PromptImageRef } from './promptImageRefs';

/** IC caps the mention grid at 36 candidates. */
export const MENTION_CANDIDATE_LIMIT = 36;

interface Props {
  images: PromptImageRef[];
  onPick: (image: PromptImageRef) => void;
}

export function MentionImageGrid({ images, onPick }: Props): React.ReactElement {
  return (
    <div
      data-testid="mention-image-grid"
      // Opens DOWNWARD (IC does): anchored above, it covered the input-image
      // row and collided with the count/size popovers that live over the
      // footer. z-60 so it wins against those (they sit at z-50) — it is the
      // thing the user just summoned.
      className="mh-pop-in nodrag nowheel absolute left-0 top-full z-[60] mt-1 w-[26rem] rounded-xl border border-canvas-line bg-canvas-card p-2 shadow-lg"
      onMouseDown={(e) => e.preventDefault()}
    >
      {images.length === 0 ? (
        <p className="px-1 py-2 text-[10px] text-canvas-muted">No input images on this node</p>
      ) : (
        <div className="grid max-h-48 grid-cols-4 gap-1.5 overflow-y-auto">
          {images.slice(0, MENTION_CANDIDATE_LIMIT).map((img) => (
            <button
              key={img.url}
              type="button"
              data-testid="mention-image-option"
              onMouseDown={(e) => {
                e.preventDefault();
                onPick(img);
              }}
              className="nodrag group flex flex-col items-center gap-0.5"
            >
              <span className="h-12 w-12 overflow-hidden rounded-md border border-canvas-line/60">
                <img
                  src={mediaSrc(img.url)}
                  alt=""
                  className="h-full w-full object-cover"
                />
              </span>
              <span className="truncate text-[9px] text-canvas-muted group-hover:text-canvas-text">
                {img.alias}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
