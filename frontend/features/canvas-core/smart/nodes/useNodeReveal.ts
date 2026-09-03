// features/canvas-core/smart/nodes/useNodeReveal.ts
//
// "Is this card's floating toolbar worth mounting right now?" (canvas
// fluency Wave 2, Task 5).
//
// The node toolbars used to be permanently in the DOM and merely faded to
// `opacity-0`. A frosted island costs the compositor a backdrop blur and a
// soft shadow whether or not you can see it, on every frame of every drag
// and pan, once per card. So they are mounted on demand instead — and this
// hook is the demand signal.
//
// TWO flags, not one. Pointer and focus reveal independently, because they
// leave independently: moving the mouse off a card whose toolbar currently
// holds the keyboard focus must not rip the focused button out of the
// document. Merging them into a single boolean is exactly that bug.
//
// State is LOCAL to the node view on purpose — Task 4 wrapped the views in
// `memo`, and hover living in the store or in node `data` would re-render
// every other node on every pointer move.

import { useMemo, useState, type FocusEvent } from 'react';

export interface NodeRevealHandlers {
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  onFocus: () => void;
  onBlur: (event: FocusEvent<HTMLElement>) => void;
}

export function useNodeReveal(): {
  revealed: boolean;
  revealHandlers: NodeRevealHandlers;
} {
  const [pointerOver, setPointerOver] = useState(false);
  const [focusInside, setFocusInside] = useState(false);

  const revealHandlers = useMemo<NodeRevealHandlers>(
    () => ({
      onMouseEnter: () => setPointerOver(true),
      onMouseLeave: () => setPointerOver(false),
      onFocus: () => setFocusInside(true),
      onBlur: (event) => {
        // `focusout` fires BEFORE the matching `focusin`, so a naive
        // setFocusInside(false) would unmount the toolbar in the gap while
        // tabbing from one of its buttons to the next. `relatedTarget` is
        // where focus is heading: still inside this card means stay put.
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setFocusInside(false);
        }
      },
    }),
    [],
  );

  return { revealed: pointerOver || focusInside, revealHandlers };
}
