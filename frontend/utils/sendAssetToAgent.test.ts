/**
 * sendAssetToAgent — the shared helper both Send To Agent entry points call.
 *
 * The two things worth pinning are the two things that have gone wrong before
 * on the resource side of this feature: the channel payload (a field dropped
 * here is a chip that renders blank and an asset the backend never sees), and
 * ruling I — that this path does NOT run `ensureResourceProcessed`. That
 * helper charges points for transcription on a MEDIA row; an asset has no
 * media to process, so triggering it would bill the user for nothing.
 *
 * The two CALL SITES have their own tests (`SheetSidebar.sendToAgent.test.tsx`
 * and `AssetCard.sendToAgent.test.tsx`) — a helper that works while one menu
 * never calls it is exactly the failure this file cannot see.
 */
import fs from 'node:fs';
import path from 'node:path';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useGlobalChatStore } from '../stores/globalChatStore';
import { sendAssetToAgent } from './sendAssetToAgent';

/** A real `GET /assets` row (ids are strings; presets carry a null scope). */
const CHARACTER = {
  id: '727145299382534300',
  name: 'Sang Yao',
  asset_type: 'character',
  cover_file_id: '727145299382534146',
  scope_id: '727145299382534200',
};

const t = (_key: string, def: string, opts?: Record<string, unknown>): string => {
  let out = def;
  for (const [k, v] of Object.entries(opts ?? {})) out = out.split(`{{${k}}}`).join(String(v));
  return out;
};

beforeEach(() => {
  useGlobalChatStore.setState({ open: false, pendingAsset: null, pendingResource: null });
});

describe('sendAssetToAgent', () => {
  it('opens the chat and stages the asset on the pendingAsset channel', () => {
    sendAssetToAgent(CHARACTER, { addToast: vi.fn(), t });

    const state = useGlobalChatStore.getState();
    expect(state.open).toBe(true);
    expect(state.pendingAsset).toEqual({
      assetId: '727145299382534300',
      loadoutId: null,
      name: 'Sang Yao',
      assetType: 'character',
      coverFileId: '727145299382534146',
      scopeId: '727145299382534200',
      nonce: 1,
    });
  });

  it('sends loadoutId null — v1 offers no loadout picker at either entry', () => {
    sendAssetToAgent(CHARACTER, { addToast: vi.fn(), t });
    // Null is the backend's "use the default loadout" (ruling D), not a gap.
    expect(useGlobalChatStore.getState().pendingAsset?.loadoutId).toBeNull();
  });

  it('carries a system preset through with null cover and null scope', () => {
    sendAssetToAgent(
      { id: '727145299382534303', name: 'Multi-angle 3x3 sheet', asset_type: 'prompt' },
      { addToast: vi.fn(), t },
    );
    const pending = useGlobalChatStore.getState().pendingAsset;
    expect(pending?.coverFileId).toBeNull();
    expect(pending?.scopeId).toBeNull();
  });

  it('confirms the send with a toast naming the asset', () => {
    // The composer can be off-screen when the click lands, so without this
    // the shelf's Send To Agent looks exactly like a no-op.
    const addToast = vi.fn();
    sendAssetToAgent(CHARACTER, { addToast, t });
    expect(addToast).toHaveBeenCalledWith('Added Sang Yao To The Chat Composer', 'info');
  });

  it('still stages when there is no toast host', () => {
    expect(() => sendAssetToAgent(CHARACTER, { addToast: null, t })).not.toThrow();
    expect(useGlobalChatStore.getState().pendingAsset?.assetId).toBe('727145299382534300');
  });

  it('does nothing at all for an asset with no id', () => {
    const addToast = vi.fn();
    sendAssetToAgent({ id: '', name: 'x', asset_type: 'prop' }, { addToast, t });
    expect(useGlobalChatStore.getState().pendingAsset).toBeNull();
    // And no toast either — a confirmation for a send that did not happen is
    // worse than silence.
    expect(addToast).not.toHaveBeenCalled();
  });

  it('leaves a staged resource alone', () => {
    useGlobalChatStore.getState().sendResourceToChat({
      resourceId: '7301234567890123456',
      name: 'Interview Take 3.mp4',
      kind: 'video',
      mime: 'video/mp4',
      scope: { type: 'personal', id: 'u-1' },
    });
    sendAssetToAgent(CHARACTER, { addToast: vi.fn(), t });
    expect(useGlobalChatStore.getState().pendingResource?.name).toBe('Interview Take 3.mp4');
  });

  it('does not import the AI processing top-up (ruling I)', () => {
    // A structural assertion, because a behavioural one would need the module
    // to be imported to be spied on — which is the very thing being ruled
    // out. `ensureResourceProcessed` bills points against a resource row; an
    // asset has none, so this path must not reach it even transitively.
    const src = fs.readFileSync(
      path.resolve(__dirname, 'sendAssetToAgent.ts'),
      'utf8',
    );
    // Matching the IMPORT and the CALL, not the identifier: the module's own
    // header explains why it stays away, and a bare substring check would
    // fire on that sentence — a guard that fails on its own documentation
    // gets deleted, not fixed.
    expect(src).toContain('sendAssetToChat');   // the guard ran on the right file
    expect(src).not.toMatch(/import[^;]*ensureResourceProcessed/);
    expect(src).not.toMatch(/ensureResourceProcessed\s*\(/);
  });
});
