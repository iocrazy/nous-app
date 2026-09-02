// features/canvas-core/smart/mediaImport.role.test.ts
//
// The `role` form field: which of the three things `POST /generated-media/
// import` is being used for on this call.
//
// Without it every mask and brush composite the canvas editors bake lands in
// the Generated inbox looking exactly like a file the user chose to upload —
// the inbox has no other way to tell them apart, because they share an
// origin_kind, a storage path and a card shape.

import { describe, expect, it, vi } from 'vitest';

const mockApiFetch = vi.fn();
vi.mock('../../../services/apiClient', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
}));

import { importCanvasMedia } from './mediaImport';

const ok = () =>
  mockApiFetch.mockResolvedValue({
    json: async () => ({
      data: { id: 'gm-1', url: '/api/v1/generated-media/gm-1/cover', media_kind: 'image' },
    }),
  });

/** The FormData the one call was made with. */
function sentForm(): FormData {
  const [, init] = mockApiFetch.mock.calls.at(-1) as [string, { raw: FormData }];
  return init.raw;
}

describe('importCanvasMedia — role', () => {
  it.each(['mask', 'brush'] as const)('sends role=%s when the caller classifies', async (role) => {
    ok();
    const file = new File([new Uint8Array([1])], `${role}.png`, { type: 'image/png' });

    await importCanvasMedia(file, 'c1', 'n1', role);

    expect(sentForm().get('role')).toBe(role);
  });

  it('omits the field entirely when the caller says nothing', async () => {
    // The backend's "absent means user_upload, which is visible" rule lives
    // in ONE place. A client that spelled the default out would be a second
    // copy of it, free to drift.
    ok();
    const file = new File([new Uint8Array([1])], 'photo.png', { type: 'image/png' });

    await importCanvasMedia(file, 'c1', 'n1');

    expect(sentForm().has('role')).toBe(false);
  });

  it('omits it for an explicit user_upload too', async () => {
    ok();
    const file = new File([new Uint8Array([1])], 'photo.png', { type: 'image/png' });

    await importCanvasMedia(file, 'c1', 'n1', 'user_upload');

    expect(sentForm().has('role')).toBe(false);
  });

  it('still carries canvas_id and node_id alongside it', async () => {
    // The role is additive — a regression that replaced the form rather than
    // appending to it would detach the upload from its node.
    ok();
    const file = new File([new Uint8Array([1])], 'mask.png', { type: 'image/png' });

    await importCanvasMedia(file, 'c1', 'n1', 'mask');

    const form = sentForm();
    expect(form.get('canvas_id')).toBe('c1');
    expect(form.get('node_id')).toBe('n1');
    expect(form.get('role')).toBe('mask');
  });
});
