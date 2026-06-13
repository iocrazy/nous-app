import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';

export interface IslandWorkValue {
  /** Portal target for a page-provided right info island (null in classic mode). */
  infoIslandRef: React.RefObject<HTMLDivElement | null>;
  infoVisible: boolean;
  setInfoVisible: (v: boolean) => void;
  infoWidth: number;            // clamped 250–480 (spec §2)
  setInfoWidth: (w: number) => void;
  /** True when the current page provides an info island (gates the reopen tab). */
  infoAvailable: boolean;
  setInfoAvailable: (v: boolean) => void;
  /** True only inside an island shell — pages branch on this. */
  active: boolean;
}

const INERT: IslandWorkValue = {
  infoIslandRef: { current: null },
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
  const infoIslandRef = useRef<HTMLDivElement | null>(null);
  const [infoVisible, setInfoVisible] = useState(false);
  const [infoWidth, setInfoWidthState] = useState(360);
  const [infoAvailable, setInfoAvailableState] = useState(false);
  const setInfoWidth = useCallback(
    (w: number) => setInfoWidthState(Math.min(480, Math.max(250, w))),
    [],
  );
  const setInfoAvailable = useCallback((v: boolean) => setInfoAvailableState(v), []);
  const value = useMemo(
    () => ({
      infoIslandRef,
      infoVisible,
      setInfoVisible,
      infoWidth,
      setInfoWidth,
      infoAvailable,
      setInfoAvailable,
      active: true,
    }),
    [infoVisible, infoWidth, setInfoWidth, infoAvailable, setInfoAvailable],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

export function useIslandWork(): IslandWorkValue {
  return useContext(Ctx);
}
