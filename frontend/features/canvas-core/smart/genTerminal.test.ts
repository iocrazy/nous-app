// features/canvas-core/smart/genTerminal.test.ts
//
// 「已经生成好了图片，为什么后面还有一块区域在闪烁？」——用户实拍，2026-09-08。
// 同一个症状 2026-09-02 已经报过一次（`healGenSlots.ts` 的头注释就是那次写的）。
//
// 那次的修法是**载入时**把无主的 `gen_pending` 归零。但这个不变式并不是在载入
// 时被破坏的，而是在 run 进终态那一刻——就在用户眼皮底下。所以上一版的效果是：
// 闪烁要等到用户碰巧刷新页面才停。
//
// 这次实测把机理钉死了：出问题的那个节点持久化下来是 `gen_pending=0` / 1 张图 /
// `gen_ratio="21:9"`，而那张图从对象存储读出来正好是 1916×821（= 2.3337，精确
// 21:9）。图和框完全吻合，所以「没有 pending」这个状态**渲染不出任何空白块**——
// 截图里那块只能是 `mh-loading-cell`，也就是 `gen_pending ≥ 1`。它后来变成 0，
// 是因为画布被重载、载入时的 heal 兜住了。
//
// 不变式写在这里：**run 一进终态，它的槽位就不能再有 pending 单元。**
// 这个时刻是可证的——`onItemSettled` 在每个 task 自己的 `.then` 里跑，而终态是
// `Promise.all` resolve 之后才发的，所以到这一刻每个派发出去的 task 都已经结算过。
// 还留着的 pending 就是没有任何人会去结算的。

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { onGenerationTerminal } from './dispatchEffects';
import type { CanvasNode } from '../types';

const PROMPT: CanvasNode = {
  id: 'p1',
  type: 'prompt',
  position: { x: 0, y: 0 },
  data: { body: 'x', provider_slug: '', agent_id: null, run_status: 'running', resource_refs: [] },
};

/** A slot mid-run: one image landed, one cell still promised. */
function slot(pending: number, images = 1): CanvasNode {
  return {
    id: 'out1',
    type: 'output',
    position: { x: 400, y: 0 },
    data: {
      kind: 'image',
      images: Array.from({ length: images }, (_, i) => ({ url: `/u/${i}`, kind: 'image' })),
      gen_pending: pending,
      gen_failed: 0,
      gen_ratio: '21:9',
      gen_slot: { node_id: 'p1', index: 0 },
    },
  } as unknown as CanvasNode;
}

function seed(pending: number): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart', canvasId: '9', nodes: [PROMPT, slot(pending)], connections: [], selection: [],
  });
}

const slotData = () =>
  (useCanvasCoreStore.getState().nodes.find((n) => (n as { id: string }).id === 'out1') as
    { data: Record<string, unknown> }).data;

afterEach(() => {
  useCanvasCoreStore.getState().reset();
  vi.restoreAllMocks();
});

describe('run 进终态时，槽位不能再有 pending 单元', () => {
  it('清掉没人会结算的 pending 单元——不必等用户刷新', () => {
    seed(1);
    onGenerationTerminal('p1');
    expect(slotData().gen_pending).toBe(0);
    // 已经到手的图一张都不能动：这是清理，不是回滚。
    expect((slotData().images as unknown[]).length).toBe(1);
  });

  it('把漏掉的单元喊出来——它一直没被解释，正是因为无声地修好了', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    seed(2);
    onGenerationTerminal('p1');
    expect(warn).toHaveBeenCalled();
    expect(String(warn.mock.calls[0]?.join(' '))).toContain('p1');
  });

  it('正常收尾不出声，也不写节点', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    seed(0);
    const before = useCanvasCoreStore.getState().nodes;
    onGenerationTerminal('p1');
    expect(warn).not.toHaveBeenCalled();
    // 同一个数组：终态每次 run 都会走到，无谓的写会churn 节点标识、脏画布、
    // 还会把一次纯运行时的收尾变成一次文档改动。
    expect(useCanvasCoreStore.getState().nodes).toBe(before);
  });

  it('没有槽位的 run（纯文本）什么也不做', () => {
    useCanvasCoreStore.getState().reset();
    useCanvasCoreStore.setState({ kind: 'smart', canvasId: '9', nodes: [PROMPT], connections: [], selection: [] });
    expect(() => onGenerationTerminal('p1')).not.toThrow();
  });
});

/**
 * 同 `dispatchEffects.test.ts` 末尾那两条的用意：修法不是「在漏掉的那处补一个
 * 调用」——那正是当初两个入口漂开的原因。写终态的每一处都必须走同一个效果，
 * 下一个入口点想自己另写一套就会在这里失败。
 */
describe('每个写终态的入口都要走同一个效果', () => {
  const read = (rel: string) => readFileSync(join(__dirname, rel), 'utf8');

  it('三个写终态的地方都调用 onGenerationTerminal', () => {
    for (const rel of ['CanvasComposer.tsx', 'regenerate.ts', 'genResume.ts']) {
      expect(read(rel), `${rel} 写了终态却没结算槽位——闪烁会留在完成的节点上`).toMatch(
        /onGenerationTerminal/,
      );
    }
  });
});
