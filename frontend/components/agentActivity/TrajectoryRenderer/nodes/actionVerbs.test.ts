import { describe, expect, it } from 'vitest';

import { actionLabel } from './actionVerbs';

describe('actionLabel', () => {
  it('把已知工具译成动词 + 对象', () => {
    expect(actionLabel('ReadScene', { scene_id: 7 }, true)).toBe('Read scene 7');
    expect(actionLabel('ListShots', { scene_id: 7 }, true)).toBe('Listed shots');
    expect(actionLabel('UpdateShot', { shot_id: '42' }, true)).toBe('Edited shot 42');
    expect(actionLabel('GenerateShotImage', { shot_id: 42 }, true)).toBe('Generated image 42');
    expect(actionLabel('RunCommand', { command: 'ls -la' }, true)).toBe('Ran ls -la');
  });

  it('失败的动作在末尾说清它失败了——只说做了什么会读成成功', () => {
    expect(actionLabel('GenerateShotImage', { shot_id: 42 }, false)).toBe('Generated image 42 failed');
    expect(actionLabel('ListShots', {}, false)).toBe('Listed shots failed');
  });

  it('没映射的工具原样用工具名 + 主参数，不猜动词', () => {
    expect(actionLabel('Skill', { skill: 'script-outline' }, true)).toBe('Skill script-outline');
    expect(actionLabel('Delegate', { description: 'research the venue' }, true)).toBe('Delegate research the venue');
    expect(actionLabel('Mystery', {}, true)).toBe('Mystery');
  });

  it('args 不是对象、或主参数缺席时只留动词', () => {
    expect(actionLabel('UpdateShot', null, true)).toBe('Edited shot');
    expect(actionLabel('UpdateShot', 'not-an-object', true)).toBe('Edited shot');
    expect(actionLabel('ReadScene', { scene_id: null }, true)).toBe('Read scene');
  });

  it('长参数截断', () => {
    expect(actionLabel('RunCommand', { command: 'x'.repeat(80) }, true)).toBe(`Ran ${'x'.repeat(40)}…`);
  });
});
