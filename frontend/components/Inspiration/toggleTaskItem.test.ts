import { describe, expect, it } from 'vitest';
import { toggleTaskItem } from './toggleTaskItem';

describe('toggleTaskItem', () => {
  it('checks an unchecked item', () => {
    expect(toggleTaskItem('- [ ] buy milk', 0)).toBe('- [x] buy milk');
  });

  it('unchecks a checked item', () => {
    expect(toggleTaskItem('- [x] done', 0)).toBe('- [ ] done');
  });

  it('toggles the Nth item in document order, leaving others', () => {
    const md = '- [ ] a\n- [ ] b\n- [ ] c';
    expect(toggleTaskItem(md, 1)).toBe('- [ ] a\n- [x] b\n- [ ] c');
  });

  it('handles *, + and indented markers', () => {
    expect(toggleTaskItem('  * [ ] nested', 0)).toBe('  * [x] nested');
    expect(toggleTaskItem('+ [ ] plus', 0)).toBe('+ [x] plus');
  });

  it('uppercase X counts as checked and toggles off', () => {
    expect(toggleTaskItem('- [X] up', 0)).toBe('- [ ] up');
  });

  it('does not count a bracket inside a fenced code block', () => {
    const md = 'real:\n- [ ] task\n```\n- [ ] fake in code\n```';
    // index 0 is the real task; index 1 must not exist → out of range returns unchanged
    expect(toggleTaskItem(md, 0)).toBe('real:\n- [x] task\n```\n- [ ] fake in code\n```');
    expect(toggleTaskItem(md, 1)).toBe(md);
  });

  it('out-of-range index returns the source unchanged', () => {
    expect(toggleTaskItem('- [ ] only', 5)).toBe('- [ ] only');
  });
});
