/**
 * §5.3: buildScriptContext turns a ContextCapsule value into the structured
 * selection handle sent alongside a chat turn. Pure function — no rendering,
 * just the id-carrying transform (see AIChatPanel.tsx's docstring on it for
 * why quoted_text is deliberately NOT included: the capsule text is already
 * folded into the outgoing message content by handleSend).
 */

import { describe, expect, it } from 'vitest';

import { buildScriptContext } from './AIChatPanel';

describe('buildScriptContext', () => {
  it('returns null when there is no capsule', () => {
    expect(buildScriptContext(null)).toBeNull();
  });

  it('returns null when the capsule has no scene/element id', () => {
    expect(buildScriptContext({ text: 'x' })).toBeNull();
  });

  it('builds the handle when the capsule carries ids', () => {
    expect(
      buildScriptContext({
        text: 'x',
        sceneId: '31415',
        elementId: 'el_1',
        elementType: 'dialogue',
        sceneLabel: 'S2',
      }),
    ).toEqual({
      scene_id: '31415',
      element_ids: ['el_1'],
      element_type: 'dialogue',
      scene_label: 'S2',
      cross_scene: false,
    });
  });
});
