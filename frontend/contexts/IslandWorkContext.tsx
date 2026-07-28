import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { loadPanelWidth } from '../components/detail/DetailCardKit';

/** Clamp range shared by `setInfoWidth` (context) and the drag handler (IslandShell). */
export const INFO_WIDTH_MIN = 250;
export const INFO_WIDTH_MAX = 640;
/** Persistence key — this info island is shared by the uploads (ResourceInfoPanel)
 *  and downloads (DownloadInfoPanel, island mode) sidebars, so one key covers both. */
export const INFO_WIDTH_STORAGE_KEY = 'resources-info';

export interface IslandWorkValue {
  /** The attached info-island portal target (null until mounted). Pages portal into this. */
  infoIslandEl: HTMLDivElement | null;
  /** Callback ref — attach to the shell's info-island portal target div. */
  setInfoIslandEl: (el: HTMLDivElement | null) => void;
  infoVisible: boolean;
  setInfoVisible: (v: boolean) => void;
  infoWidth: number;            // clamped 250–640 (drag range widened per feedback)
  setInfoWidth: (w: number) => void;
  /** True when the current page provides an info island (gates the reopen tab). */
  infoAvailable: boolean;
  setInfoAvailable: (v: boolean) => void;
  /** True only inside an island shell — pages branch on this. */
  active: boolean;
}

const INERT: IslandWorkValue = {
  infoIslandEl: null,
  setInfoIslandEl: () => {},
  infoVisible: false,
  setInfoVisible: () => {},
  infoWidth: 360,
  setInfoWidth: () => {},
  infoAvailable: false,
  setInfoAvailable: () => {},
  active: false,
};

const Ctx = createContext<IslandWorkValue>(INERT);

export const IslandWorkProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [infoIslandEl, setInfoIslandElState] = useState<HTMLDivElement | null>(null);
  const [infoVisible, setInfoVisible] = useState(false);
  const [infoWidth, setInfoWidthState] = useState(() =>
    loadPanelWidth(INFO_WIDTH_STORAGE_KEY, 360, INFO_WIDTH_MIN, INFO_WIDTH_MAX),
  );
  const [infoAvailable, setInfoAvailableState] = useState(false);
  const setInfoIslandEl = useCallback((el: HTMLDivElement | null) => setInfoIslandElState(el), []);
  const setInfoWidth = useCallback(
    (w: number) => setInfoWidthState(Math.min(INFO_WIDTH_MAX, Math.max(INFO_WIDTH_MIN, w))),
    [],
  );
  const setInfoAvailable = useCallback((v: boolean) => setInfoAvailableState(v), []);
  const value = useMemo(
    () => ({
      infoIslandEl,
      setInfoIslandEl,
      infoVisible,
      setInfoVisible,
      infoWidth,
      setInfoWidth,
      infoAvailable,
      setInfoAvailable,
      active: true,
    }),
    [infoIslandEl, setInfoIslandEl, infoVisible, infoWidth, setInfoWidth, infoAvailable, setInfoAvailable],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

export function useIslandWork(): IslandWorkValue {
  return useContext(Ctx);
}
