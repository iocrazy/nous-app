// features/canvas-core/smart/nodes/useAgents.ts
//
// AI Library agent catalog for the prompt node's agent picker (CC3).
// Mirrors useTextModels: fetched once per session (module cache — agents
// change via the AI Library settings page, not mid-edit), failures degrade
// to an empty list (picker shows only "No agent") and log for the funnel.

import { useEffect, useState } from 'react';

import { aiLibraryService } from '../../../../services/aiLibraryService';

export interface CanvasAgentOption {
  id: string;
  slug: string;
  name: string;
}

let cache: CanvasAgentOption[] | null = null;
let inflight: Promise<CanvasAgentOption[]> | null = null;

/** Test hook: reset the module cache between cases. */
export function _resetAgentsCache(): void {
  cache = null;
  inflight = null;
}

async function fetchAgents(): Promise<CanvasAgentOption[]> {
  const agents = await aiLibraryService.listAgents();
  return agents.map((a) => ({
    id: String((a as { id: unknown }).id),
    slug: a.slug,
    name: a.name || a.slug,
  }));
}

export function useAgents(): CanvasAgentOption[] {
  const [agents, setAgents] = useState<CanvasAgentOption[]>(cache ?? []);

  useEffect(() => {
    if (cache) return undefined;
    let live = true;
    inflight = inflight ?? fetchAgents();
    inflight
      .then((a) => {
        cache = a;
        if (live) setAgents(a);
      })
      .catch((err: unknown) => {
        console.error('[useAgents] agent catalog fetch failed:', err);
      });
    return () => {
      live = false;
    };
  }, []);

  return agents;
}
