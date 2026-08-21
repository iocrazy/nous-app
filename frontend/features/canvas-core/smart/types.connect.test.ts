// IC parity: a generated OUTPUT feeds downstream prompts/loops directly —
// "生成的图片也能有输出节点" (2026-08-21). Group collection stays.
import { describe, expect, it } from 'vitest';

import { canConnectSmart } from './types';

describe('canConnectSmart output-as-source', () => {
  it('output wires into prompt, loop and group; not into media/shot', () => {
    expect(canConnectSmart('output', 'prompt')).toBe(true);
    expect(canConnectSmart('output', 'loop')).toBe(true);
    expect(canConnectSmart('output', 'group')).toBe(true);
    expect(canConnectSmart('output', 'media')).toBe(false);
    expect(canConnectSmart('output', 'shot')).toBe(false);
  });
});
