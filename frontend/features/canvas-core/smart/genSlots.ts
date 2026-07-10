// features/canvas-core/smart/genSlots.ts
//
// Per-prompt generation output slot (Infinite-Canvas parity G4-F2). ALL of
// a run's result URLs land in ONE output node right of the prompt (Infinite:
// count N = one node, N images), tagged data.gen_slot {node_id} and reused
// on re-runs — the previous set is archived into the slot's history node
// (replaceOutputImagesWithHistory). Insertion is history-free: operational
// output must not evict the user's undo stack.

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasConnection, CanvasNode } from '../types';
import { createOutputNode } from './factories';
import { replaceOutputImagesWithHistory } from './outputHistory';
import type { GeneratedImageRef, OutputKind } from './types';

const SLOT_OFFSET_X = 320;

interface GenSlotTag {
  node_id: string;
  index: number;
}

const asObj = (n: unknown) => n as Record<string, unknown>;

function genSlotTagOf(node: unknown): GenSlotTag | undefined {
  const data = asObj(node).data as { gen_slot?: GenSlotTag } | undefined;
  return data?.gen_slot;
}

/** Create-or-update the output slot for one prompt's generation results. */
export function upsertGenerationSlots(
  promptId: string,
  urls: string[],
  mediaKind: OutputKind,
): void {
  const store = useCanvasCoreStore.getState();
  const prompt = store.nodes.find((n) => asObj(n).id === promptId);
  if (!prompt) return;

  const existing = store.nodes.find((n) => genSlotTagOf(n)?.node_id === promptId);
  if (existing) {
    replaceOutputImagesWithHistory(String(asObj(existing).id), urls, mediaKind);
    return;
  }

  const promptPos =
    (asObj(prompt).position as { x: number; y: number }) ?? { x: 0, y: 0 };
  const base = createOutputNode(
    { kind: mediaKind, preview_url: urls[0] ?? null, preview_text: '' },
    { position: { x: promptPos.x + SLOT_OFFSET_X, y: promptPos.y } },
  );
  const slot = {
    ...base,
    data: {
      ...(base.data as unknown as Record<string, unknown>),
      images: urls.map((url): GeneratedImageRef => ({ url, kind: mediaKind })),
      gen_slot: { node_id: promptId, index: 0 } satisfies GenSlotTag,
    },
  } as CanvasNode;
  const edge: CanvasConnection = {
    id: `edge-${crypto.randomUUID()}`,
    source: promptId,
    target: String(asObj(slot).id),
    sourceHandle: null,
    targetHandle: null,
  };
  useCanvasCoreStore.getState().appendElementsNoHistory([slot], [edge]);
}
