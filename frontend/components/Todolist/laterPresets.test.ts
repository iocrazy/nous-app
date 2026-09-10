import { describe, expect, it } from 'vitest';

import { wakeupPresets } from './laterPresets';

describe('wakeupPresets', () => {
  it('offers an hour from now, tonight and tomorrow morning', () => {
    const now = new Date('2026-09-10T09:15:00');
    const presets = wakeupPresets(now);
    expect(presets.map((p) => p.key)).toEqual(['in1h', 'tonight', 'tomorrow']);
    expect(presets[0].at.getTime()).toBe(now.getTime() + 3600_000);
    expect(presets[1].at.getHours()).toBe(20);
    expect(presets[1].at.getDate()).toBe(10);
    expect(presets[2].at.getHours()).toBe(9);
    expect(presets[2].at.getDate()).toBe(11);
  });

  it('drops Tonight once 20:00 has passed — a preset in the past is a rejected POST', () => {
    const presets = wakeupPresets(new Date('2026-09-10T21:30:00'));
    expect(presets.map((p) => p.key)).toEqual(['in1h', 'tomorrow']);
  });

  it('crosses a month boundary correctly', () => {
    const tomorrow = wakeupPresets(new Date('2026-09-30T22:00:00')).find((p) => p.key === 'tomorrow');
    expect(tomorrow?.at.getMonth()).toBe(9); // October
    expect(tomorrow?.at.getDate()).toBe(1);
  });

  it('zeroes seconds so the scheduled minute is the one shown', () => {
    const tonight = wakeupPresets(new Date('2026-09-10T09:15:42.500')).find((p) => p.key === 'tonight');
    expect(tonight?.at.getSeconds()).toBe(0);
    expect(tonight?.at.getMilliseconds()).toBe(0);
  });
});
