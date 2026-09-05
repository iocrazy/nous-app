// features/canvas-core/library/mentionHandles.test.ts
//
// The registry that lets the PANEL reach a node's body editor.
//
// What is worth pinning is not the Map — it is the two things a Map alone gets
// wrong here. A registration must hand back the exact undo for ITSELF (an
// unregister keyed only by node id would let an unmounting card evict the
// remount that already replaced it, and the panel would then say "open the
// prompt node" about a node that is open), and a missing handle must come back
// as `null` rather than `undefined` so the caller's refusal branch is one test.

import { afterEach, describe, expect, it, vi } from 'vitest';

import { getMentionHandle, registerMentionHandle } from './mentionHandles';
import type { MentionInserters } from './mentionLibraryItems';

function fakeHandle(): MentionInserters {
  return { insertImage: vi.fn(), insertAsset: vi.fn() };
}

const undos: Array<() => void> = [];
function register(nodeId: string, handle: MentionInserters): () => void {
  const undo = registerMentionHandle(nodeId, handle);
  undos.push(undo);
  return undo;
}

afterEach(() => {
  while (undos.length > 0) undos.pop()?.();
});

describe('mentionHandles', () => {
  it('hands back the live object, not a copy — an editor handle is not data', () => {
    const handle = fakeHandle();
    register('p1', handle);
    expect(getMentionHandle('p1')).toBe(handle);
  });

  it('answers null for a node that never registered', () => {
    expect(getMentionHandle('nope')).toBeNull();
  });

  it('the returned unregister drops it, and getMentionHandle then says null', () => {
    const undo = register('p1', fakeHandle());
    undo();
    expect(getMentionHandle('p1')).toBeNull();
  });

  it('a stale unregister cannot evict the registration that replaced it', () => {
    // React runs the new effect's setup before the old one's cleanup on a
    // remount, so a cleanup that deleted by id alone would take the LIVE
    // handle out — leaving the panel refusing to insert into a node that is
    // mounted and typing.
    const first = fakeHandle();
    const second = fakeHandle();
    const undoFirst = register('p1', first);
    register('p1', second);
    undoFirst();
    expect(getMentionHandle('p1')).toBe(second);
  });

  it('unregistering twice is harmless', () => {
    const undo = register('p1', fakeHandle());
    undo();
    undo();
    expect(getMentionHandle('p1')).toBeNull();
  });
});
