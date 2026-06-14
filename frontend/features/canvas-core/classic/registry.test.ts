/**
 * ClassicMode typed-port registry + connect-rule tests (Phase 5a B2).
 *
 * ClassicMode (ComfyUI-style) nodes carry MULTIPLE typed ports
 * (image / text / prompt), unlike SmartMode's boolean handles. A wire is
 * only valid when the source node's OUTPUT port (looked up by the React
 * Flow `sourceHandle`) has the same port TYPE as the target node's INPUT
 * port (looked up by `targetHandle`).
 */

import { describe, expect, it } from 'vitest';

import {
  CLASSIC_PORT_TYPES,
  canConnectClassic,
  classicNodeDefinitions,
  getClassicNodeDefinition,
} from './registry';

describe('classicNodeDefinitions', () => {
  it('defines the registered node types', () => {
    expect(Object.keys(classicNodeDefinitions).sort()).toEqual(
      ['comfy', 'image', 'llm', 'note', 'output', 'prompt', 'text'].sort(),
    );
  });

  it('uses only known port types', () => {
    for (const def of Object.values(classicNodeDefinitions)) {
      for (const port of [...def.inputs, ...def.outputs]) {
        expect(CLASSIC_PORT_TYPES).toContain(port.type);
      }
    }
  });

  it('gives every port a unique handle id within a node', () => {
    for (const def of Object.values(classicNodeDefinitions)) {
      const ids = [...def.inputs, ...def.outputs].map((p) => p.id);
      expect(new Set(ids).size).toBe(ids.length);
    }
  });

  it('image node outputs an image port; output node takes an image port', () => {
    expect(classicNodeDefinitions.image.outputs.some((p) => p.type === 'image')).toBe(true);
    expect(classicNodeDefinitions.output.inputs.some((p) => p.type === 'image')).toBe(true);
  });

  it('llm takes prompt + optional image, outputs text', () => {
    const llm = classicNodeDefinitions.llm;
    expect(llm.inputs.some((p) => p.type === 'prompt')).toBe(true);
    expect(llm.inputs.some((p) => p.type === 'image')).toBe(true);
    expect(llm.outputs.some((p) => p.type === 'text')).toBe(true);
  });

  it('comfy emits BOTH image and text on different handles', () => {
    const outs = classicNodeDefinitions.comfy.outputs;
    expect(outs.some((p) => p.type === 'image')).toBe(true);
    expect(outs.some((p) => p.type === 'text')).toBe(true);
    expect(outs.find((p) => p.type === 'image')?.id).not.toBe(
      outs.find((p) => p.type === 'text')?.id,
    );
  });

  it('getClassicNodeDefinition resolves a known type and returns undefined otherwise', () => {
    expect(getClassicNodeDefinition('image')).toBe(classicNodeDefinitions.image);
    expect(getClassicNodeDefinition('nope')).toBeUndefined();
  });

  it('text is a static source: one text output, zero inputs', () => {
    const text = classicNodeDefinitions.text;
    expect(text.inputs).toEqual([]);
    expect(text.outputs).toHaveLength(1);
    expect(text.outputs[0].type).toBe('text');
  });

  it('note is a portless annotation: zero inputs AND zero outputs', () => {
    const note = classicNodeDefinitions.note;
    expect(note.inputs).toEqual([]);
    expect(note.outputs).toEqual([]);
  });
});

describe('canConnectClassic', () => {
  const imgOut = classicNodeDefinitions.image.outputs.find((p) => p.type === 'image')!.id;
  const outImgIn = classicNodeDefinitions.output.inputs.find((p) => p.type === 'image')!.id;
  const llmTextOut = classicNodeDefinitions.llm.outputs.find((p) => p.type === 'text')!.id;
  const comfyImgOut = classicNodeDefinitions.comfy.outputs.find((p) => p.type === 'image')!.id;
  const comfyTextOut = classicNodeDefinitions.comfy.outputs.find((p) => p.type === 'text')!.id;

  it('image-output → image-input ✓ (matching port types)', () => {
    expect(canConnectClassic('image', 'output', imgOut, outImgIn)).toBe(true);
  });

  it('image-output → text-input ✗ (port type mismatch)', () => {
    // llm has a prompt input but no plain text input; connect an image
    // output into llm's prompt input → mismatch.
    const llmPromptIn = classicNodeDefinitions.llm.inputs.find((p) => p.type === 'prompt')!.id;
    expect(canConnectClassic('image', 'llm', imgOut, llmPromptIn)).toBe(false);
  });

  it('text-output → image-input ✗ (port type mismatch)', () => {
    expect(canConnectClassic('llm', 'output', llmTextOut, outImgIn)).toBe(false);
  });

  it('multi-output comfy: image handle → image input ✓', () => {
    expect(canConnectClassic('comfy', 'output', comfyImgOut, outImgIn)).toBe(true);
  });

  it('multi-output comfy: image handle → image input ✗ when using the TEXT handle', () => {
    // Same target image input, but the text output handle → mismatch.
    expect(canConnectClassic('comfy', 'output', comfyTextOut, outImgIn)).toBe(false);
  });

  it('unknown source handle id rejected', () => {
    expect(canConnectClassic('image', 'output', 'does-not-exist', outImgIn)).toBe(false);
  });

  it('unknown target handle id rejected', () => {
    expect(canConnectClassic('image', 'output', imgOut, 'does-not-exist')).toBe(false);
  });

  it('handle that exists but on the wrong side is rejected (output handle used as input)', () => {
    // imgOut is an OUTPUT handle; using it as the TARGET (input) lookup fails.
    expect(canConnectClassic('image', 'image', imgOut, imgOut)).toBe(false);
  });

  it('unknown node type rejected', () => {
    expect(canConnectClassic('nope', 'output', imgOut, outImgIn)).toBe(false);
    expect(canConnectClassic('image', 'nope', imgOut, outImgIn)).toBe(false);
  });

  it('null/undefined handles rejected', () => {
    expect(canConnectClassic('image', 'output', null, outImgIn)).toBe(false);
    expect(canConnectClassic('image', 'output', imgOut, undefined)).toBe(false);
  });

  // ---- text source node ---------------------------------------------------

  const textOut = classicNodeDefinitions.text.outputs.find((p) => p.type === 'text')!.id;
  const llmTextIn = classicNodeDefinitions.llm.inputs.find((p) => p.type === 'text')!.id;

  it('text-output → llm text-input ✓ (matching text port types)', () => {
    expect(canConnectClassic('text', 'llm', textOut, llmTextIn)).toBe(true);
  });

  it('text-output → output image-input ✗ (text vs image mismatch)', () => {
    expect(canConnectClassic('text', 'output', textOut, outImgIn)).toBe(false);
  });

  // ---- portless note node -------------------------------------------------

  it('a portless note can never be a connection target (no input handle found)', () => {
    // Any handle id on a note is "not found" → rejected.
    expect(canConnectClassic('text', 'note', textOut, 'note-in')).toBe(false);
    expect(canConnectClassic('text', 'note', textOut, textOut)).toBe(false);
  });

  it('a portless note can never be a connection source (no output handle found)', () => {
    expect(canConnectClassic('note', 'llm', 'note-out', llmTextIn)).toBe(false);
    expect(canConnectClassic('note', 'output', textOut, outImgIn)).toBe(false);
  });
});
