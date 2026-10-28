"use client";

import { createContext, useContext, useState, useSyncExternalStore, type ReactNode } from "react";
import { HorizonController, type PlayerSnapshot } from "./horizon-controller";

const HorizonContext = createContext<HorizonController | null>(null);

/**
 * Shares one player between the gate's lit door and the horizon. Renders no element of its
 * own, so the hero grid keeps its rows.
 */
export function HorizonProvider({ duration, children }: { duration: number; children: ReactNode }) {
  const [controller] = useState(() => new HorizonController(duration));
  return <HorizonContext.Provider value={controller}>{children}</HorizonContext.Provider>;
}

export function useHorizonController(): HorizonController {
  const c = useContext(HorizonContext);
  if (!c) throw new Error("useHorizonController needs a <HorizonProvider>");
  return c;
}

/** The coarse player state: what the buttons should say. */
export function useHorizonState(): PlayerSnapshot {
  const c = useHorizonController();
  return useSyncExternalStore(c.subscribe, c.getSnapshot, c.getServerSnapshot);
}
