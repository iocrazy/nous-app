import React, { useEffect, useRef } from 'react';

export interface ContextMenuItem {
  label: string;
  icon?: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
  divider?: boolean;
}

interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
}

export const ContextMenu: React.FC<ContextMenuProps> = ({ x, y, items, onClose }) => {
  const menuRef = useRef<HTMLDivElement>(null);

  // Adjust position to stay within viewport
  useEffect(() => {
    const el = menuRef.current;
    if (!el) return;

    const rect = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let adjustedX = x;
    let adjustedY = y;

    if (rect.right > vw) {
      adjustedX = vw - rect.width - 8;
    }
    if (rect.bottom > vh) {
      adjustedY = vh - rect.height - 8;
    }
    if (adjustedX < 0) adjustedX = 8;
    if (adjustedY < 0) adjustedY = 8;

    el.style.left = `${adjustedX}px`;
    el.style.top = `${adjustedY}px`;
  }, [x, y]);

  // Close on click outside
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    // Use setTimeout to avoid the same right-click event from closing the menu
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClick);
      document.addEventListener('keydown', handleKeyDown);
    }, 0);

    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [onClose]);

  return (
    <div
      ref={menuRef}
      className="fixed z-50 bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl py-1 min-w-[180px] animate-in fade-in zoom-in-95 duration-100"
      style={{ left: x, top: y }}
    >
      {items.map((item, idx) => (
        <React.Fragment key={idx}>
          {item.divider && <div className="my-1 border-t border-zinc-700/50" />}
          <button
            onClick={() => {
              if (!item.disabled) {
                item.onClick();
                onClose();
              }
            }}
            disabled={item.disabled}
            className={`
              w-full flex items-center gap-2.5 px-3 py-1.5 text-sm text-left transition-colors
              ${item.disabled
                ? 'text-zinc-600 cursor-not-allowed'
                : item.danger
                  ? 'text-red-400 hover:bg-red-900/30 hover:text-red-300'
                  : 'text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100'
              }
            `}
          >
            {item.icon && <span className="shrink-0 w-4 flex items-center justify-center">{item.icon}</span>}
            <span>{item.label}</span>
          </button>
        </React.Fragment>
      ))}
    </div>
  );
};
