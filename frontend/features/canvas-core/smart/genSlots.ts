// features/canvas-core/smart/genSlots.ts
//
// Per-prompt generation output slots (Infinite-Canvas parity G4-F1). Each
// result URL lands in an output node to the right of its prompt, stacked
// vertically, tagged data.gen_slot {node_id, index} and REUSED on re-runs
// (same contract as the loop slots — F2 upgrades this to the images[] +
// history-archive semantics). Insertion is history-free: operational
// output must not evict the user's undo stack.

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasConnection, CanvasNode } from '../types';
import { createOutputNode } from './factories';
import type { OutputKind } from './types';

const SLOT_OFFSET_X = 320;
const SLOT_SPACING_Y = 150;

interface GenSlotTag {
  node_id: string;
  index: number;
}

const asObj = (n: unknown) => n as Record<string, unknown>;

function genSlotTagOf(node: unknown): GenSlotTag | undefined {
  const data = asObj(node).data as { gen_slot?: GenSlotTag } | undefined;
  return data?.gen_slot;
}

/** Create-or-update the output slots for one prompt's generation results. */
export function upsertGenerationSlots(
  promptId: string,
  urls: string[],
  mediaKind: OutputKind,
): void {
  const store = useCanvasCoreStore.getState();
  const prompt = store.nodes.find((n) => asObj(n).id === promptId);
  if (!prompt) return;
  const promptPos =
    (asObj(prompt).position as { x: number; y: number }) ?? { x: 0, y: 0 };

  const newNodes: CanvasNode[] = [];
  const newConnections: CanvasConnection[] = [];

  urls.forEach((url, index) => {
    const existing = store.nodes.find((n) => {
      const tag = genSlotTagOf(n);
      return tag?.node_id === promptId && tag?.index === index;
    });
    if (existing) {
      store.patchNode(String(asObj(existing).id), {
        data: { kind: mediaKind, preview_url: url, preview_text: '' },
      });
      return;
    }
    const base = createOutputNode(
      { kind: mediaKind, preview_url: url, preview_text: '' },
      {
        position: {
          x: promptPos.x + SLOT_OFFSET_X,
          y: promptPos.y + index * SLOT_SPACING_Y,
        },
      },
    );
    const slot = {
      ...base,
      data: {
        ...(base.data as unknown as Record<string, unknown>),
        gen_slot: { node_id: promptId, index } satisfies GenSlotTag,
      },
    } as CanvasNode;
    newNodes.push(slot);
    newConnections.push({
      id: `edge-${crypto.randomUUID()}`,
      source: promptId,
      target: String(asObj(slot).id),
      sourceHandle: null,
      targetHandle: null,
    });
  });

  if (newNodes.length > 0 || newConnections.length > 0) {
    useCanvasCoreStore
      .getState()
      .appendElementsNoHistory(newNodes, newConnections);
  }
}
