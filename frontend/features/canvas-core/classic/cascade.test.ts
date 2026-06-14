import { afterEach, describe, expect, it, vi } from 'vitest';

import { runClassicCascade, type CascadeHandlers } from './cascade';
import type { ClassicRunner } from './classicRunner';
import {
  clearAllAbortControllers,
  getAbortSignal,
  hasAbortController,
} from './abortRegistry';
import type { CanvasConnection, CanvasNode } from '../types';

afterEach(() => {
  clearAllAbortControllers();
});

// ---- builders -------------------------------------------------------------

function node(
  id: string,
  type: string,
  data: Record<string, unknown> = {},
): CanvasNode {
  // comfy nodes need a workflow_slug to be runnable; default one in.
  const base = type === 'comfy' ? { workflow_slug: `wf-${id}` } : {};
  return { id, type, data: { ...base, ...data } };
}

function edge(source: string, target: string): CanvasConnection {
  return { id: `${source}->${target}`, source, target };
}

// ---- recording handlers ---------------------------------------------------

interface Patch {
  id: string;
  run_status: string;
  run_error?: string | null;
}

function recorder(): {
  patches: Patch[];
  toasts: string[];
  statusOf: (id: string) => string | undefined;
  handlers: CascadeHandlers;
} {
  const patches: Patch[] = [];
  const toasts: string[] = [];
  return {
    patches,
    toasts,
    statusOf: (id) => {
      const last = [...patches].reverse().find((p) => p.id === id);
      return last?.run_status;
    },
    handlers: {
      onNodePatch: (id, patch) =>
        patches.push({ id, run_status: patch.run_status, run_error: patch.run_error }),
      onToast: (msg) => toasts.push(msg),
      now: () => '2026-06-14T00:00:00Z',
    },
  };
}

const okRunner: ClassicRunner = async () => ({ ok: true, text: 'ok', error: null });

function failRunnerFor(failId: string): ClassicRunner {
  return async (ctx) =>
    ctx.nodeId === failId
      ? { ok: false, text: '', error: `boom@${ctx.nodeId}` }
      : { ok: true, text: 'ok', error: null };
}

// ---- tests ----------------------------------------------------------------

describe('runClassicCascade — success chain A→B→C', () => {
  it('runs all in order, none blocked, all succeeded', async () => {
    const nodes = [node('A', 'comfy'), node('B', 'comfy'), node('C', 'comfy')];
    const conns = [edge('A', 'B'), edge('B', 'C')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(ran).toEqual(['A', 'B', 'C']);
    expect(report.succeeded).toEqual(['A', 'B', 'C']);
    expect(report.failed).toEqual([]);
    expect(report.blocked).toEqual([]);
    expect(r.toasts).toEqual([]);
    expect(r.statusOf('C')).toBe('succeeded');
  });
});

describe('runClassicCascade — failure mid-chain A→B(fail)→C→D', () => {
  it('marks B failed, C+D blocked, A succeeded, never runs C/D, toasts at B', async () => {
    const nodes = [
      node('A', 'comfy'),
      node('B', 'comfy', { label: 'Render B' }),
      node('C', 'comfy'),
      node('D', 'comfy'),
    ];
    const conns = [edge('A', 'B'), edge('B', 'C'), edge('C', 'D')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return ctx.nodeId === 'B'
        ? { ok: false, text: '', error: 'comfy exploded' }
        : { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(report.succeeded).toEqual(['A']);
    expect(report.failed).toEqual(['B']);
    expect(report.blocked.sort()).toEqual(['C', 'D']);
    // C and D were never dispatched.
    expect(ran).toEqual(['A', 'B']);
    // B carries its run_error inline.
    const bFail = r.patches.find((p) => p.id === 'B' && p.run_status === 'failed');
    expect(bFail?.run_error).toBe('comfy exploded');
    // Failure is NEVER silent: a top-level toast names the failed node.
    expect(r.toasts).toContain('Cascade stopped at Render B');
    expect(r.statusOf('C')).toBe('blocked');
    expect(r.statusOf('D')).toBe('blocked');
  });
});

describe('runClassicCascade — diamond A→B,A→C,B→D,C→D', () => {
  it('B fails → D blocked (via B), C still runs/succeeds, A succeeded', async () => {
    const nodes = [
      node('A', 'comfy'),
      node('B', 'comfy'),
      node('C', 'comfy'),
      node('D', 'comfy'),
    ];
    const conns = [edge('A', 'B'), edge('A', 'C'), edge('B', 'D'), edge('C', 'D')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return ctx.nodeId === 'B'
        ? { ok: false, text: '', error: 'B broke' }
        : { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(report.succeeded.sort()).toEqual(['A', 'C']);
    expect(report.failed).toEqual(['B']);
    expect(report.blocked).toEqual(['D']);
    // C runs (not downstream of B); D never runs (blocked via B).
    expect(ran).toContain('C');
    expect(ran).not.toContain('D');
    expect(r.statusOf('C')).toBe('succeeded');
    expect(r.statusOf('D')).toBe('blocked');
  });
});

describe('runClassicCascade — unknown node type', () => {
  it('unknown WITH downstream is a contained failure (blocks downstream), no throw', async () => {
    const nodes = [node('X', 'mystery'), node('Y', 'comfy')];
    const conns = [edge('X', 'Y')];
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, okRunner, r.handlers);

    expect(report.failed).toEqual(['X']);
    expect(report.blocked).toEqual(['Y']);
    const xFail = r.patches.find((p) => p.id === 'X' && p.run_status === 'failed');
    expect(xFail?.run_error).toMatch(/no provider mapping/);
    expect(r.toasts.some((t) => t.startsWith('Cascade stopped at'))).toBe(true);
  });

  it('isolated unknown (no downstream) is skipped with a console.warn, cascade continues', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const nodes = [node('A', 'comfy'), node('Z', 'mystery')];
    const conns: CanvasConnection[] = []; // Z has no downstream, A independent
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, okRunner, r.handlers);

    expect(report.succeeded).toEqual(['A']);
    expect(report.failed).toEqual([]);
    expect(report.skipped).toContain('Z');
    expect(warn).toHaveBeenCalled();
    expect(r.toasts).toEqual([]);
    warn.mockRestore();
  });
});

describe('runClassicCascade — passive nodes', () => {
  it('skips a passive prompt source but still runs the downstream llm', async () => {
    const nodes = [node('P', 'prompt'), node('L', 'llm', { model: 'qwen' })];
    const conns = [edge('P', 'L')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(ran).toEqual(['L']); // prompt source not dispatched
    expect(report.skipped).toContain('P');
    expect(report.succeeded).toContain('L');
  });
});

describe('runClassicCascade — portless note node', () => {
  it('a portless note alongside a runnable chain is skipped, chain runs, no throw, no toast', async () => {
    // P(prompt, passive) → L(llm, runnable); N(note, portless) is isolated.
    const nodes = [
      node('P', 'prompt'),
      node('L', 'llm', { model: 'qwen' }),
      node('N', 'note', { text: 'remember to set seed' }),
    ];
    const conns = [edge('P', 'L')]; // note has NO edges
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    // The runnable chain runs normally.
    expect(ran).toEqual(['L']);
    expect(report.succeeded).toContain('L');
    // The portless note is skipped (passive), never dispatched, stays idle.
    expect(report.skipped).toContain('N');
    expect(ran).not.toContain('N');
    expect(r.statusOf('N')).toBeUndefined(); // no patch emitted → still idle
    // Nothing failed/blocked and no toast fired.
    expect(report.failed).toEqual([]);
    expect(report.blocked).toEqual([]);
    expect(r.toasts).toEqual([]);
  });
});

describe('runClassicCascade — passive preview sink downstream', () => {
  it('prompt→llm→preview: preview (passive sink) is skipped, the chain runs, no throw', async () => {
    // P(prompt, passive) → L(llm, runnable) → V(preview, passive display sink).
    const nodes = [
      node('P', 'prompt'),
      node('L', 'llm', { model: 'qwen' }),
      node('V', 'preview'),
    ];
    const conns = [edge('P', 'L'), edge('L', 'V')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    // Only the runnable llm dispatches; both passive ends are skipped.
    expect(ran).toEqual(['L']);
    expect(report.succeeded).toContain('L');
    expect(report.skipped).toContain('P');
    expect(report.skipped).toContain('V');
    expect(ran).not.toContain('V');
    // preview stays idle (no patch), nothing failed/blocked, no toast.
    expect(r.statusOf('V')).toBeUndefined();
    expect(report.failed).toEqual([]);
    expect(report.blocked).toEqual([]);
    expect(r.toasts).toEqual([]);
  });
});

describe('runClassicCascade — portless group container', () => {
  it('a portless group alongside a runnable chain is skipped, chain runs, no throw, no toast', async () => {
    // P(prompt, passive) → L(llm, runnable); G(group, portless) is isolated.
    const nodes = [
      node('P', 'prompt'),
      node('L', 'llm', { model: 'qwen' }),
      node('G', 'group', { label: 'Render group' }),
    ];
    const conns = [edge('P', 'L')]; // group has NO edges
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    // The runnable chain runs normally.
    expect(ran).toEqual(['L']);
    expect(report.succeeded).toContain('L');
    // The portless group is skipped (passive), never dispatched, stays idle.
    expect(report.skipped).toContain('G');
    expect(ran).not.toContain('G');
    expect(r.statusOf('G')).toBeUndefined();
    // Nothing failed/blocked and no toast fired.
    expect(report.failed).toEqual([]);
    expect(report.blocked).toEqual([]);
    expect(r.toasts).toEqual([]);
  });
});

describe('runClassicCascade — abort wiring', () => {
  it('passes the beginAbortable signal for the node into the run call', async () => {
    const nodes = [node('N', 'comfy')];
    const conns: CanvasConnection[] = [];
    let seenSignal: AbortSignal | undefined;
    let registeredDuringRun = false;
    const runner: ClassicRunner = async (ctx, signal) => {
      seenSignal = signal;
      // The signal handed to the runner is the live one in the registry.
      registeredDuringRun =
        hasAbortController(ctx.nodeId) && getAbortSignal(ctx.nodeId) === signal;
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(seenSignal).toBeInstanceOf(AbortSignal);
    expect(registeredDuringRun).toBe(true);
    // Settled normally → controller cleared (no leak).
    expect(hasAbortController('N')).toBe(false);
  });
});
