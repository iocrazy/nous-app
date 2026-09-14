/**
 * 3b §3.4 — one spelling of a produced version's price, for all four faces.
 *
 * The two facts under test are the ones four hand-written copies would each
 * get differently: `allocated` has to LOOK approximate, and "no price" must
 * never render as `¢0.000` (a call that reached a model is not free).
 */
import { describe, expect, it } from 'vitest';

import { formatOutputCost, outputCostTitle } from './outputCost';

describe('outputCost', () => {
  it('marks an allocated cost as approximate', () => {
    expect(formatOutputCost(0.09, 'allocated')).toBe('≈¢0.09');
    expect(outputCostTitle(0.09, 'allocated', { deliverableKind: 'script_shot', model: 'x' }))
      .toBe('Allocated from step cost');
  });

  it('an exact price carries no ≈ and no title', () => {
    expect(formatOutputCost(12, 'exact')).toBe('¢12.00');
    expect(outputCostTitle(12, 'exact', { deliverableKind: 'generated_media', model: 'gpt-6-astra' })).toBeUndefined();
  });

  it('no price reads as unknown, and says why only for media', () => {
    // 0 不是「免费」：到过模型的调用在价表配齐时永远不是 0
    // （builtins.tsx 的 fmtChildCents 写下的同一条规则）。
    expect(formatOutputCost(null, null)).toBe('—');
    expect(formatOutputCost(0, 'exact')).toBe('—');
    expect(outputCostTitle(null, null, { deliverableKind: 'generated_media', model: 'gpt-6-astra' }))
      .toBe('No price configured for gpt-6-astra');
    expect(outputCostTitle(null, null, { deliverableKind: 'script_shot', model: 'qwen-max' })).toBeUndefined();
  });

  it('keeps sub-cent costs readable', () => expect(formatOutputCost(0.004, 'allocated')).toBe('≈¢0.004'));

  it('a price too small to print never rounds down to a fabricated zero', () => {
    // `¢0.000` states the work was free, which is the one thing we know is
    // false — it has a price, it is just below what three decimals can show.
    // `<` already carries the imprecision, so no `≈` is added on top of it.
    expect(formatOutputCost(0.0004, 'allocated')).toBe('<¢0.001');
    expect(formatOutputCost(0.0004, 'exact')).toBe('<¢0.001');
    expect(formatOutputCost(0.0004, null)).toBe('<¢0.001');
    // the boundary still prints, because it rounds to something non-zero
    expect(formatOutputCost(0.0005, 'exact')).toBe('¢0.001');
  });
});
