/**
 * Append an asset card to a canvas the user is NOT currently editing
 * (P4 Task 6 — the asset sheet's "Send To Canvas").
 *
 * This is a document write from outside the store: the target canvas has no
 * mounted `canvasCoreStore`, so the whole read-modify-write happens here
 * against the REST client, and the optimistic lock has to be honoured by hand.
 *
 * Three things this deliberately does, each with an obvious wrong version:
 *
 *  * IT REFUSES A READ-ONLY CANVAS UP FRONT. `GET /canvases/{id}` ships
 *    `can_edit`, which is the same verdict the PUT's write guard reaches. A
 *    doomed PUT would answer 403 half a second later and the user would have
 *    been told "sent" in the meantime.
 *  * ONE CONFLICT IS RETRIED, THE SECOND IS REPORTED. Appending a node is
 *    commutative with whatever the other writer did, so rebasing onto the
 *    server's current row and trying again is correct rather than merely
 *    convenient — and the 409 body already carries that row, so the retry
 *    costs no extra request. A SECOND conflict means the canvas is being
 *    written continuously; looping would be an unbounded write against a board
 *    someone else is using, so it stops and says so.
 *  * EVERY FAILURE IS TYPED. The caller has to be able to say WHICH thing went
 *    wrong (`read_only` and `conflict` want different sentences, and neither is
 *    "something went wrong"). A thrown error would collapse them.
 *
 * The node id is minted per attempt, together with the position — a retry
 * re-derives both from the newer row, so the card cannot land on top of a node
 * the other writer added.
 */

import { createAssetNode } from '../smart/factories';
import type { AssetNodeSeed } from '../smart/assetFiles';
import { nextFreePosition } from '../smart/assetPlacement';
import type { Canvas, CanvasNode, CanvasSaveResult, CanvasUpdatePayload } from '../types';
import { getCanvas, saveCanvas } from './canvasService';

/** Why the send did not happen. Each maps to its own user-visible sentence. */
export type SendFailureReason =
  | 'load_failed'
  | 'read_only'
  | 'conflict'
  | 'save_failed';

export type SendAssetToCanvasResult =
  | { ok: true; canvasId: string; nodeId: string }
  | { ok: false; reason: SendFailureReason; message: string };

export interface SendAssetToCanvasDeps {
  load?: (canvasId: string) => Promise<Canvas>;
  save?: (canvasId: string, payload: CanvasUpdatePayload) => Promise<CanvasSaveResult>;
  /** Loadout to bind on the new card (a character's active outfit). */
  loadoutId?: string | null;
}

const MESSAGE_OF = (err: unknown): string =>
  err instanceof Error ? err.message : String(err);

export async function sendAssetToCanvas(
  canvasId: string,
  asset: AssetNodeSeed,
  deps: SendAssetToCanvasDeps = {},
): Promise<SendAssetToCanvasResult> {
  const load = deps.load ?? getCanvas;
  const save = deps.save ?? saveCanvas;
  const loadoutId = deps.loadoutId ?? null;

  let base: Canvas;
  try {
    base = await load(canvasId);
  } catch (err) {
    console.error('[sendAssetToCanvas] could not load the target canvas:', err);
    return { ok: false, reason: 'load_failed', message: MESSAGE_OF(err) };
  }

  // `undefined` means "this payload carries no permission statement", NOT
  // false — only an explicit `false` refuses (see `Canvas.can_edit`).
  if (base.can_edit === false) {
    return { ok: false, reason: 'read_only', message: 'read_only' };
  }

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const existing: CanvasNode[] = base.nodes_json ?? [];
    const node = createAssetNode(asset, {
      position: nextFreePosition(existing),
      loadoutId,
    }) as CanvasNode;

    let saved: CanvasSaveResult;
    try {
      saved = await save(canvasId, {
        base_updated_at: base.base_updated_at,
        nodes_json: [...existing, node],
      });
    } catch (err) {
      console.error('[sendAssetToCanvas] save failed:', err);
      return { ok: false, reason: 'save_failed', message: MESSAGE_OF(err) };
    }

    // `=== true`, not a bare `if (saved.ok)`. This project compiles WITHOUT
    // `strictNullChecks`, and outside it a bare truthiness test does not
    // narrow a boolean-discriminated union — `saved.conflict` on the other arm
    // then fails to typecheck. An explicit literal comparison does narrow.
    if (saved.ok === true) {
      return { ok: true, canvasId, nodeId: String((node as { id: string }).id) };
    } else {
      base = saved.conflict;
    }
  }

  return {
    ok: false,
    reason: 'conflict',
    message: 'the canvas changed while the card was being added',
  };
}
