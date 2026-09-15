/**
 * The two caps, pinned on the TypeScript side (B10).
 *
 * ⚠️ **This is not the drift guard.** The pair is enforced by
 * `backend/tests/services/ai/chat/test_attachment_limit_frontend_mirror.py`,
 * which reads BOTH files; a TS-side test can only ever hardcode the number a
 * third time and would stay green while diverging from Python. What this file
 * adds is the other half of the shape: a PR that touches only `frontend/**`
 * never runs the backend suite (`ci.yml` runs by changed area), so until now
 * the two constants could be edited here with nothing failing anywhere.
 *
 * The expected values are copied from the backend declarations, named here so
 * the next reader can check them without guessing where they live:
 *
 *   MAX_ASSET_REF_ATTACHMENTS  = 8
 *     backend/app/services/ai/chat/ai_library_chat_service.py:101
 *   MAX_OUTPUT_REF_ATTACHMENTS = 8
 *     backend/app/services/ai/chat/output_ref_resolver.py:58
 *
 * They are asserted SEPARATELY and hold the same number today by coincidence:
 * one bounds about five serial queries per asset, the other one `lineage_for`
 * per cited object (the TS file's own comments say so). A single assertion
 * over both would pass forever and stop meaning anything the day one moves.
 */
import { describe, expect, it } from 'vitest';

import { MAX_ASSET_REF_ATTACHMENTS, MAX_OUTPUT_REF_ATTACHMENTS } from './attachmentLimits';

describe('attachment caps', () => {
  it('asset refs: 8, as ai_library_chat_service declares', () => {
    expect(MAX_ASSET_REF_ATTACHMENTS).toBe(8);
  });

  it('output citations: 8, as output_ref_resolver declares', () => {
    expect(MAX_OUTPUT_REF_ATTACHMENTS).toBe(8);
  });

  // NOT asserted here: "each is its own declaration rather than one
  // re-exported under two names". Both are primitives holding the same value,
  // so nothing at runtime can tell those two files apart — it takes a read of
  // the SOURCE, which `test_the_two_caps_are_read_from_their_own_declarations`
  // already does with a regex over this file.
});
