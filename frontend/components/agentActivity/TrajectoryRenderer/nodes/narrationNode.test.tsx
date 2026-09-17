/**
 * 3c §4.1 — 叙述与动作交错。
 *
 * 一段阶段性叙述与最终回答出自同一个说话人，所以用同一个渲染器；一行动作要读成
 * 「做了什么」，工具名只是它的实现细节。
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { NarrationNode, StepNode } from '../foldEvents';
import { NarrationNodeView } from './NarrationNode';
import { StepNodeView } from './builtins';

// i18n mock：与 nodes/outputCards.test.tsx:18-26 逐字同一段（模板 + {{var}} 插值）。
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, opts?: unknown) => {
      const template = typeof fallback === 'string' ? fallback : key;
      const vars = (typeof fallback === 'object' ? fallback : opts) as Record<string, unknown> | undefined;
      return vars ? template.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(vars[k] ?? `{{${k}}}`)) : template;
    },
  }),
}));

afterEach(cleanup);

const narration: NarrationNode = {
  kind: 'narration',
  key: 'narration:3',
  text: 'Here is **where** things stand.',
  step: 1,
  at: '2026-09-15T00:00:00Z',
};

const step = (tool: string, args: unknown, ok: boolean): StepNode => ({
  kind: 'step',
  key: 'step:1:1',
  turn: 1,
  step: 1,
  live: false,
  model: 'm',
  startedAt: null,
  lines: [
    {
      key: 'tool:4',
      type: 'tool',
      label: tool,
      ok,
      count: 1,
      durationMs: 1200,
      detail: { tool, args, iteration: 1, timedOut: false, timeoutS: null, elapsedS: null },
    },
  ],
  summary: { tools: 1, retries: 0, compactions: 0, outputs: 0, todo: null, durationMs: 1200, costCents: null, finishReason: null },
  children: [],
  outputs: [],
});

describe('NarrationNode', () => {
  it('渲成正文，markdown 生效（与最终回答同一个渲染器）', () => {
    render(<NarrationNodeView node={narration} expanded={false} />);
    const body = screen.getByTestId('traj-narration');
    expect(body.textContent).toContain('Here is where things stand.');
    expect(body.querySelector('strong')?.textContent).toBe('where');
  });
});

describe('StepNodeView 的工具行读动作动词', () => {
  it('成功的一行说做了什么，而不是调了谁', () => {
    render(<StepNodeView node={step('UpdateShot', { shot_id: '42' }, true)} expanded />);
    expect(document.body.textContent).toContain('Edited shot 42');
    expect(document.body.textContent).not.toContain('UpdateShot');
  });

  it('失败的一行把 failed 写进标签', () => {
    render(<StepNodeView node={step('GenerateShotImage', { shot_id: 42 }, false)} expanded />);
    expect(document.body.textContent).toContain('Generated image 42 failed');
  });
});
// NarrationNodeView 的 `expanded` / `onToggle` 由 NodeProps 要求，此处传死值即可。
