import React, { useRef, useEffect, useCallback, useState } from 'react';
import { createPortal } from 'react-dom';
import { EagleTagBrowser } from './EagleTagBrowser';
import type { Tag } from '../../types';
import type { PickerSettings, PanelSize } from '../../services/tagPreferencesService';

interface FloatingPanelProps {
  triggerRef: React.RefObject<HTMLElement | null>;
  allTags: Tag[];
  selectedIds: Set<string>;
  starredIds: string[];
  settings: PickerSettings;
  panelSize: PanelSize;
  onToggleTag: (tagId: string) => void;
  onToggleStar: (tagId: string) => void;
  onUpdateSettings: (partial: Partial<PickerSettings>) => void;
  onPanelResize: (size: PanelSize) => void;
  onClose: () => void;
  onCreate?: (name: string, color: string) => Promise<Tag | null>;
}

export const FloatingPanel: React.FC<FloatingPanelProps> = ({
  triggerRef,
  allTags,
  selectedIds,
  starredIds,
  settings,
  panelSize,
  onToggleTag,
  onToggleStar,
  onUpdateSettings,
  onPanelResize,
  onClose,
  onCreate,
}) => {
  const panelRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState(panelSize);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const resizeRef = useRef<{ startX: number; startY: number; startW: number; startH: number } | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Detect mobile
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 640);
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < 640);
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, []);

  // Position panel to the left of trigger (desktop only)
  useEffect(() => {
    if (isMobile) return; // Mobile uses full-screen layout
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const panelW = size.width;
    const panelH = size.height;

    let left = rect.left - panelW - 8;
    let top = rect.top;

    // Fallback: if not enough space on left, try right
    if (left < 8) {
      left = rect.right + 8;
    }
    // Fallback: if not enough space on right either, center
    if (left + panelW > window.innerWidth - 8) {
      left = Math.max(8, (window.innerWidth - panelW) / 2);
    }
    // Vertical bounds
    if (top + panelH > window.innerHeight - 8) {
      top = Math.max(8, window.innerHeight - panelH - 8);
    }

    setPosition({ top, left });
  }, [triggerRef, size.width, size.height, isMobile]);

  // Close on ESC
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    // Use setTimeout to avoid closing immediately from the same click that opened
    const timer = setTimeout(() => document.addEventListener('mousedown', handler), 0);
    return () => { clearTimeout(timer); document.removeEventListener('mousedown', handler); };
  }, [onClose]);

  // Resize handlers
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    resizeRef.current = { startX: e.clientX, startY: e.clientY, startW: size.width, startH: size.height };
    const handleMove = (me: MouseEvent) => {
      if (!resizeRef.current) return;
      const newW = Math.min(1200, Math.max(300, resizeRef.current.startW + me.clientX - resizeRef.current.startX));
      const newH = Math.min(800, Math.max(250, resizeRef.current.startH + me.clientY - resizeRef.current.startY));
      setSize({ width: newW, height: newH });
    };
    const handleUp = () => {
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
      resizeRef.current = null;
      // Debounced persist
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        setSize((s) => { onPanelResize(s); return s; });
      }, 500);
    };
    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
  }, [size, onPanelResize]);

  const browser = (
    <EagleTagBrowser
      allTags={allTags}
      selectedIds={selectedIds}
      starredIds={starredIds}
      settings={settings}
      onToggleTag={onToggleTag}
      onToggleStar={onToggleStar}
      onUpdateSettings={onUpdateSettings}
      onCreate={onCreate}
      onClose={onClose}
      forceMobileLayout={isMobile}
      className="flex-1"
    />
  );

  // Mobile: full-screen bottom sheet
  if (isMobile) {
    return createPortal(
      <>
        {/* Backdrop */}
        <div className="fixed inset-0 bg-black/60 z-[59]" onClick={onClose} />
        <div
          ref={panelRef}
          className="fixed inset-x-0 bottom-0 z-[60] bg-zinc-900 rounded-t-2xl flex flex-col overflow-hidden animate-in slide-in-from-bottom duration-200"
          style={{ maxHeight: '85vh' }}
        >
          {/* Drag handle */}
          <div className="flex justify-center pt-2 pb-1">
            <div className="w-10 h-1 rounded-full bg-zinc-700" />
          </div>

          {browser}
        </div>
      </>,
      document.body,
    );
  }

  // Desktop: floating panel
  return createPortal(
    <div
      ref={panelRef}
      className="fixed z-[60] bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl flex flex-col overflow-hidden"
      style={{ top: position.top, left: position.left, width: size.width, height: size.height }}
    >
      {browser}

      {/* Resize handle */}
      <div
        onMouseDown={handleResizeStart}
        className="absolute bottom-0 right-0 w-4 h-4 cursor-se-resize"
        style={{ background: 'linear-gradient(135deg, transparent 50%, rgba(113,113,122,0.4) 50%)' }}
      />
    </div>,
    document.body,
  );
};
