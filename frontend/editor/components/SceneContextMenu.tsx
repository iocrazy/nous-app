/**
 * SceneContextMenu — a small right-click menu for the script editor's scene
 * heading + element rows. Positioned at the cursor (viewport coords), it closes
 * on outside-click, Esc, scroll, or after an item runs. Items may be marked
 * `danger` (delete actions) or separated by a divider.
 *
 * It is a pure presentational popup: the owner (SceneBlock) decides which items
 * to show for a heading vs. an element row and what each does.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';

export interface SceneContextMenuItem {
  key: string;
  label: string;
  /** Runs on select; the menu closes immediately afterwards. */
  onSelect: () => void;
  /** Red styling for destructive actions (delete block / delete scene). */
  danger?: boolean;
  /** Render a divider ABOVE this item. */
  dividerBefore?: boolean;
}

export interface SceneContextMenuProps {
  /** Viewport X/Y where the menu should open (usually the contextmenu event). */
  x: number;
  y: number;
  items: SceneContextMenuItem[];
  onClose: () => void;
}

export function SceneContextMenu({ x, y, items, onClose }: SceneContextMenuProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  // Start at the raw cursor point, then clamp inside the viewport once measured.
  const [pos, setPos] = useState({ left: x, top: y });

  // Clamp so the menu never overflows the viewport edges (flip up/left if tight).
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    const margin = 8;
    const left = Math.min(x, window.innerWidth - width - margin);
    const top = Math.min(y, window.innerHeight - height - margin);
    setPos({ left: Math.max(margin, left), top: Math.max(margin, top) });
  }, [x, y]);

  // Dismiss on outside pointer, Esc, scroll, or resize — one armed listener set.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('scroll', onClose, true);
    window.addEventListener('resize', onClose);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('scroll', onClose, true);
      window.removeEventListener('resize', onClose);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="mh-scene-ctx-menu"
      role="menu"
      style={{ left: pos.left, top: pos.top }}
      // Never let a click inside bubble out to the editor / close-on-outside.
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((item) => (
        <div key={item.key}>
          {item.dividerBefore && <div className="mh-scene-ctx-divider" aria-hidden="true" />}
          <button
            type="button"
            role="menuitem"
            className={`mh-scene-ctx-item${item.danger ? ' danger' : ''}`}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              item.onSelect();
              onClose();
            }}
          >
            {item.label}
          </button>
        </div>
      ))}
    </div>
  );
}
