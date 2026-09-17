import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * One behaviour for the three control-bar popups: speed, volume, quality.
 *
 * They used to differ. Volume opened on hover; speed and quality needed a
 * click, and each owned its own boolean and its own click-away overlay. The
 * report asked for all three to open above the bar on hover and to look the
 * same, B站 being the reference. Owning the behaviour here, once, is what keeps
 * them from drifting apart again.
 *
 *   hover      open immediately, close the others, cancel any pending close
 *   leave      close after MENU_CLOSE_DELAY_MS — the pointer crosses a few
 *              pixels between button and popup, so an instant close would
 *              make the popup unreachable
 *   click      toggle, for touch and keyboard, where there is no hover
 *   tap away   close — touch never fires mouseleave, so without this a
 *              tapped-open popup would stay open indefinitely
 *
 * The popup wrappers carry `data-player-menu`, which is how a tap is told
 * apart from a tap outside.
 */

export type PlayerMenu = 'speed' | 'volume' | 'quality';

/** Long enough to cross the gap between button and popup; short enough that a
 * deliberate move away reads as "closed". */
export const MENU_CLOSE_DELAY_MS = 260;

export interface PlayerMenus {
  open: PlayerMenu | null;
  anyOpen: boolean;
  isOpen: (id: PlayerMenu) => boolean;
  toggle: (id: PlayerMenu) => void;
  closeAll: () => void;
  /** Spread on the wrapper that contains both the button and its popup. */
  hoverProps: (id: PlayerMenu) => {
    onMouseEnter: () => void;
    onMouseLeave: () => void;
  };
}

export function usePlayerMenus(): PlayerMenus {
  const [open, setOpen] = useState<PlayerMenu | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelClose = useCallback(() => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  const openNow = useCallback(
    (id: PlayerMenu) => {
      cancelClose();
      setOpen(id);
    },
    [cancelClose],
  );

  const closeSoon = useCallback(
    (id: PlayerMenu) => {
      cancelClose();
      closeTimer.current = setTimeout(() => {
        closeTimer.current = null;
        // Only close THIS menu. Sweeping from speed to quality schedules
        // speed's close and then opens quality; that close must not land on
        // quality when it fires.
        setOpen((current) => (current === id ? null : current));
      }, MENU_CLOSE_DELAY_MS);
    },
    [cancelClose],
  );

  const toggle = useCallback(
    (id: PlayerMenu) => {
      cancelClose();
      setOpen((current) => (current === id ? null : id));
    },
    [cancelClose],
  );

  const closeAll = useCallback(() => {
    cancelClose();
    setOpen(null);
  }, [cancelClose]);

  const hoverProps = useCallback(
    (id: PlayerMenu) => ({
      onMouseEnter: () => openNow(id),
      onMouseLeave: () => closeSoon(id),
    }),
    [openNow, closeSoon],
  );

  // Tap-away, only while something is open.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: Event) => {
      const target = e.target as Element | null;
      if (target && typeof target.closest === 'function' && target.closest('[data-player-menu]')) {
        return;
      }
      closeAll();
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open, closeAll]);

  useEffect(() => cancelClose, [cancelClose]);

  return {
    open,
    anyOpen: open !== null,
    isOpen: (id) => open === id,
    toggle,
    closeAll,
    hoverProps,
  };
}
