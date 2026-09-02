// features/canvas-core/smart/nodes/useGraphDerived.ts
//
// Per-node graph derivations as STORE SELECTORS (Wave 1+2 Task 4 — render
// cost).
//
// The rule these hooks exist to enforce: a node view must never subscribe to
// `s.nodes`. A drag tick replaces that array every frame, so a subscription
// to it re-renders the card no matter how well `memo`'d it is — and the
// positions in there belong to OTHER nodes, which is nothing this card
// draws. What a card actually depends on is a small projection (its input
// urls, its upstream text, whether it is a chain tail); each hook below
// subscribes to that projection with an equality that holds while the
// projection is unchanged, so unrelated drags produce zero renders.
//
// The shared `GraphIndex` behind them is an internal building block, not
// something a view subscribes to: it is keyed on the `nodes` /
// `connections` array references, so it changes every frame too.

import { useShallow } from 'zustand/react/shallow';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { chainTailsFor } from '../chainRun';
import { graphIndexFor } from '../graphIndex';
import {
  resolveSourceUrlsFromIndex,
  upstreamPromptTextFromIndex,
} from '../promptInputs';

/**
 * Durable image urls wired into this prompt, in connection order (IC's
 * 「N 输入图」row). `useShallow` keeps the previous array while the urls are
 * element-wise identical, so re-wiring an unrelated node renders nothing.
 */
export function useNodeInputUrls(id: string): string[] {
  return useCanvasCoreStore(
    useShallow((s) =>
      resolveSourceUrlsFromIndex(id, graphIndexFor(s.nodes, s.connections)),
    ),
  );
}

/** One-line preview of the upstream prompt bodies (IC's inputPromptPreview).
 *  A string — default `Object.is` equality is already stable. */
export function useUpstreamPromptText(id: string): string {
  return useCanvasCoreStore((s) =>
    upstreamPromptTextFromIndex(id, graphIndexFor(s.nodes, s.connections)),
  );
}

/** Whether this prompt is the tail of a cascade (IC canRunSmartCascade) —
 *  the only card that carries the Run-chain button. A boolean, and the set
 *  behind it is cached per graph shape, so a drag recomputes nothing. */
export function useIsChainTail(id: string): boolean {
  return useCanvasCoreStore((s) => chainTailsFor(s.nodes, s.connections).has(id));
}
