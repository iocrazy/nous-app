import { describe, expect, it } from 'vitest';

import {
  buildEffectiveData,
  inputParamKey,
  nodeOutputValue,
  type IncomingWire,
  type RecordedOutput,
} from './dataPiping';

// ---------------------------------------------------------------------------
// nodeOutputValue — what a node emits on an output handle
// ---------------------------------------------------------------------------

describe('nodeOutputValue — passive sources read from data', () => {
  it('prompt/prompt-out → data.prompt', () => {
    expect(nodeOutputValue('prompt', 'prompt-out', { prompt: 'hi' }, null)).toBe('hi');
  });

  it('text/text-out → data.text', () => {
    expect(nodeOutputValue('text', 'text-out', { text: 'static' }, null)).toBe('static');
  });

  it('image/image-out → data.image_url, falling back to data.imageUrl', () => {
    expect(nodeOutputValue('image', 'image-out', { image_url: 'a.png' }, null)).toBe('a.png');
    expect(nodeOutputValue('image', 'image-out', { imageUrl: 'b.png' }, null)).toBe('b.png');
  });

  it('returns undefined for a wrong/unknown handle on a source', () => {
    expect(nodeOutputValue('prompt', 'text-out', { prompt: 'hi' }, null)).toBeUndefined();
    expect(nodeOutputValue('prompt', 'prompt-out', {}, null)).toBeUndefined();
  });
});

describe('nodeOutputValue — runnables read from run_result', () => {
  it('image_gen/image-out → runResult.image_url', () => {
    expect(nodeOutputValue('image_gen', 'image-out', {}, { image_url: 'gen.png' })).toBe(
      'gen.png',
    );
  });

  it('llm/text-out → runResult.text', () => {
    expect(nodeOutputValue('llm', 'text-out', {}, { text: 'answer' })).toBe('answer');
  });

  it('comfy/image-out → runResult.image_url and comfy/text-out → runResult.text', () => {
    expect(nodeOutputValue('comfy', 'image-out', {}, { image_url: 'c.png' })).toBe('c.png');
    expect(nodeOutputValue('comfy', 'text-out', {}, { text: 'c-text' })).toBe('c-text');
  });

  it('video_gen/video-out → runResult.video_url', () => {
    expect(nodeOutputValue('video_gen', 'video-out', {}, { video_url: 'v.mp4' })).toBe('v.mp4');
  });

  it('returns undefined when the run_result field is missing or non-string', () => {
    expect(nodeOutputValue('image_gen', 'image-out', {}, null)).toBeUndefined();
    expect(nodeOutputValue('image_gen', 'image-out', {}, {})).toBeUndefined();
    expect(nodeOutputValue('image_gen', 'image-out', {}, { image_url: 123 })).toBeUndefined();
  });

  it('returns undefined for unknown node types or missing handle', () => {
    expect(nodeOutputValue('mystery', 'image-out', {}, { image_url: 'x' })).toBeUndefined();
    expect(nodeOutputValue(undefined, 'image-out', {}, { image_url: 'x' })).toBeUndefined();
    expect(nodeOutputValue('image_gen', null, {}, { image_url: 'x' })).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// inputParamKey — which data key a piped input fills
// ---------------------------------------------------------------------------

describe('inputParamKey — backend-matching param keys', () => {
  it('image_gen maps prompt-in→prompt, image-in→reference_image_url', () => {
    expect(inputParamKey('image_gen', 'prompt-in')).toBe('prompt');
    expect(inputParamKey('image_gen', 'image-in')).toBe('reference_image_url');
  });

  it('video_gen maps image-in→source_image_url, prompt-in→prompt', () => {
    expect(inputParamKey('video_gen', 'image-in')).toBe('source_image_url');
    expect(inputParamKey('video_gen', 'prompt-in')).toBe('prompt');
  });

  it('llm maps prompt-in/text-in→prompt, image-in→reference_image_url', () => {
    expect(inputParamKey('llm', 'prompt-in')).toBe('prompt');
    expect(inputParamKey('llm', 'text-in')).toBe('prompt');
    expect(inputParamKey('llm', 'image-in')).toBe('reference_image_url');
  });

  it('comfy maps prompt-in→prompt, image-in→reference_image_url', () => {
    expect(inputParamKey('comfy', 'prompt-in')).toBe('prompt');
    expect(inputParamKey('comfy', 'image-in')).toBe('reference_image_url');
  });

  it('sinks and sources take no run param (null)', () => {
    expect(inputParamKey('output', 'image-in')).toBeNull();
    expect(inputParamKey('preview', 'image-in')).toBeNull();
    expect(inputParamKey('prompt', 'prompt-out')).toBeNull();
    expect(inputParamKey('image_gen', undefined)).toBeNull();
    expect(inputParamKey(undefined, 'prompt-in')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// buildEffectiveData — composes the two maps with precedence
// ---------------------------------------------------------------------------

function outputs(entries: Record<string, RecordedOutput>): Map<string, RecordedOutput> {
  return new Map(Object.entries(entries));
}

describe('buildEffectiveData', () => {
  it('writes a piped prompt onto the target param key', () => {
    const incoming: IncomingWire[] = [
      { sourceId: 'P', sourceHandle: 'prompt-out', targetHandle: 'prompt-in' },
    ];
    const out = outputs({
      P: { nodeType: 'prompt', data: { prompt: 'hi' }, runResult: null },
    });
    const eff = buildEffectiveData('image_gen', {}, incoming, out);
    expect(eff.prompt).toBe('hi');
  });

  it('a connected input OVERRIDES the node own widget value (ComfyUI precedence)', () => {
    const incoming: IncomingWire[] = [
      { sourceId: 'P', sourceHandle: 'prompt-out', targetHandle: 'prompt-in' },
    ];
    const out = outputs({
      P: { nodeType: 'prompt', data: { prompt: 'piped' }, runResult: null },
    });
    const eff = buildEffectiveData('image_gen', { prompt: 'own' }, incoming, out);
    expect(eff.prompt).toBe('piped');
  });

  it('does not mutate the passed ownData object', () => {
    const own = { prompt: 'own' };
    const incoming: IncomingWire[] = [
      { sourceId: 'P', sourceHandle: 'prompt-out', targetHandle: 'prompt-in' },
    ];
    const out = outputs({
      P: { nodeType: 'prompt', data: { prompt: 'piped' }, runResult: null },
    });
    const eff = buildEffectiveData('image_gen', own, incoming, out);
    expect(own.prompt).toBe('own'); // original untouched
    expect(eff).not.toBe(own);
  });

  it('keeps the own value when the wire resolves to undefined', () => {
    const incoming: IncomingWire[] = [
      { sourceId: 'P', sourceHandle: 'prompt-out', targetHandle: 'prompt-in' },
    ];
    const out = outputs({
      P: { nodeType: 'prompt', data: {}, runResult: null }, // no data.prompt → undefined
    });
    const eff = buildEffectiveData('image_gen', { prompt: 'own' }, incoming, out);
    expect(eff.prompt).toBe('own');
  });

  it('routes a run_result image_url into video_gen source_image_url', () => {
    const incoming: IncomingWire[] = [
      { sourceId: 'G', sourceHandle: 'image-out', targetHandle: 'image-in' },
    ];
    const out = outputs({
      G: { nodeType: 'image_gen', data: {}, runResult: { image_url: 'gen.png' } },
    });
    const eff = buildEffectiveData('video_gen', {}, incoming, out);
    expect(eff.source_image_url).toBe('gen.png');
  });

  it('last wire wins when two edges target the same input', () => {
    const incoming: IncomingWire[] = [
      { sourceId: 'P1', sourceHandle: 'prompt-out', targetHandle: 'prompt-in' },
      { sourceId: 'P2', sourceHandle: 'prompt-out', targetHandle: 'prompt-in' },
    ];
    const out = outputs({
      P1: { nodeType: 'prompt', data: { prompt: 'first' }, runResult: null },
      P2: { nodeType: 'prompt', data: { prompt: 'second' }, runResult: null },
    });
    const eff = buildEffectiveData('image_gen', {}, incoming, out);
    expect(eff.prompt).toBe('second');
  });

  it('ignores a wire whose source has not been recorded yet', () => {
    const incoming: IncomingWire[] = [
      { sourceId: 'missing', sourceHandle: 'prompt-out', targetHandle: 'prompt-in' },
    ];
    const eff = buildEffectiveData('image_gen', { prompt: 'own' }, incoming, new Map());
    expect(eff.prompt).toBe('own');
  });
});
