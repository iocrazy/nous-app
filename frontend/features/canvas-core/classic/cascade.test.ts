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

/** A handle-aware wire — drives DATA PIPING (source output → target input). */
function wire(
  source: string,
  sourceHandle: string,
  target: string,
  targetHandle: string,
): CanvasConnection {
  return { id: `${source}.${sourceHandle}->${target}.${targetHandle}`, source, target, sourceHandle, targetHandle };
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

describe('runClassicCascade — image_gen / video_gen runnable AI-op nodes', () => {
  it('dispatches an image_gen node (not skipped) and marks it succeeded with its result', async () => {
    const nodes = [node('P', 'prompt'), node('G', 'image_gen', { prompt: 'a castle' })];
    const conns = [edge('P', 'G')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: '', error: null, result: { image_url: 'https://x/i.png' } };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(ran).toEqual(['G']); // prompt source passive; image_gen dispatched
    expect(report.succeeded).toEqual(['G']);
    expect(report.skipped).toContain('P');
    expect(r.statusOf('G')).toBe('succeeded');
  });

  it('threads the node id + opaque data into the runner ctx', async () => {
    const nodes = [node('V', 'video_gen', { motion: 'pan-left' })];
    let seenCtx: { nodeId: string; nodeType?: string; data?: Record<string, unknown> } | null =
      null;
    const runner: ClassicRunner = async (ctx) => {
      seenCtx = ctx;
      return { ok: true, text: '', error: null };
    };
    const r = recorder();
    await runClassicCascade(nodes, [], runner, r.handlers);

    expect(seenCtx!.nodeId).toBe('V');
    expect(seenCtx!.nodeType).toBe('video_gen');
    expect(seenCtx!.data).toMatchObject({ motion: 'pan-left' });
  });

  it('a failing video_gen blocks its downstream output node', async () => {
    const nodes = [node('V', 'video_gen', { label: 'Animate' }), node('O', 'output')];
    const conns = [edge('V', 'O')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return ctx.nodeId === 'V'
        ? { ok: false, text: '', error: 'render farm down' }
        : { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(report.failed).toEqual(['V']);
    expect(report.blocked).toEqual(['O']); // output is passive but downstream → blocked
    expect(ran).toEqual(['V']); // output never dispatched
    expect(r.toasts).toContain('Cascade stopped at Animate');
  });
});

describe('runClassicCascade — readBody derives the run body from data.prompt', () => {
  it('feeds a runnable node its data.prompt as ctx.body (the llm/comfy prompt seam)', async () => {
    // A runnable llm node whose prompt lives under data.prompt (no data.body):
    // the cascade must derive ctx.body from it so the backend adapter receives
    // the prompt. This pins the data.body || data.prompt || data.text seam.
    const nodes = [node('L', 'llm', { model: 'qwen', prompt: 'hello' })];
    let seenBody: string | undefined;
    const runner: ClassicRunner = async (ctx) => {
      seenBody = ctx.body;
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    await runClassicCascade(nodes, [], runner, r.handlers);

    expect(seenBody).toBe('hello');
  });

  it('prefers data.body over data.prompt when both are present', async () => {
    const nodes = [node('L', 'llm', { model: 'qwen', body: 'from-body', prompt: 'from-prompt' })];
    let seenBody: string | undefined;
    const runner: ClassicRunner = async (ctx) => {
      seenBody = ctx.body;
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    await runClassicCascade(nodes, [], runner, r.handlers);

    expect(seenBody).toBe('from-body');
  });
});

describe('runClassicCascade — DATA PIPING (upstream output → downstream input)', () => {
  it('pipes a prompt source value into a downstream image_gen prompt param', async () => {
    // image_gen has NO own prompt; the connected prompt node supplies it.
    const nodes = [node('P', 'prompt', { prompt: 'hi' }), node('G', 'image_gen')];
    const conns = [wire('P', 'prompt-out', 'G', 'prompt-in')];
    let seenData: Record<string, unknown> | undefined;
    const runner: ClassicRunner = async (ctx) => {
      seenData = ctx.data;
      return { ok: true, text: '', error: null, result: { image_url: 'x.png' } };
    };
    const r = recorder();
    await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(seenData!.prompt).toBe('hi');
  });

  it('a connected input OVERRIDES the node own widget value (ComfyUI precedence)', async () => {
    const nodes = [
      node('P', 'prompt', { prompt: 'piped' }),
      node('G', 'image_gen', { prompt: 'own' }),
    ];
    const conns = [wire('P', 'prompt-out', 'G', 'prompt-in')];
    let seenData: Record<string, unknown> | undefined;
    const runner: ClassicRunner = async (ctx) => {
      seenData = ctx.data;
      return { ok: true, text: '', error: null };
    };
    const r = recorder();
    await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(seenData!.prompt).toBe('piped');
  });

  it('does NOT mutate the persisted node data in place', async () => {
    const gNode = node('G', 'image_gen', { prompt: 'own' });
    const nodes = [node('P', 'prompt', { prompt: 'piped' }), gNode];
    const conns = [wire('P', 'prompt-out', 'G', 'prompt-in')];
    const runner: ClassicRunner = async () => ({ ok: true, text: '', error: null });
    const r = recorder();
    await runClassicCascade(nodes, conns, runner, r.handlers);

    // The store node's own data is untouched — piping built a per-run copy.
    expect((gNode.data as Record<string, unknown>).prompt).toBe('own');
  });

  it('pipes an image_gen run_result image_url into video_gen source_image_url', async () => {
    const nodes = [node('G', 'image_gen', { prompt: 'a cat' }), node('V', 'video_gen')];
    const conns = [wire('G', 'image-out', 'V', 'image-in')];
    const seen: Record<string, Record<string, unknown>> = {};
    const runner: ClassicRunner = async (ctx) => {
      seen[ctx.nodeId] = ctx.data;
      return ctx.nodeId === 'G'
        ? { ok: true, text: '', error: null, result: { image_url: 'gen.png' } }
        : { ok: true, text: '', error: null, result: { video_url: 'v.mp4' } };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(report.succeeded).toEqual(['G', 'V']); // topo order: producer first
    expect(seen.V.source_image_url).toBe('gen.png');
  });

  it('folds an llm top-level text result into run_result so it pipes downstream', async () => {
    // llm returns its text at the TOP level with result===null; it must still
    // surface as run_result.text so a downstream node (and the inline display)
    // can read it.
    const nodes = [node('L', 'llm', { prompt: 'expand: dragon' }), node('G', 'image_gen')];
    const conns = [wire('L', 'text-out', 'G', 'prompt-in')];
    const seen: Record<string, Record<string, unknown>> = {};
    const runner: ClassicRunner = async (ctx) => {
      seen[ctx.nodeId] = ctx.data;
      return ctx.nodeId === 'L'
        ? { ok: true, text: 'a fierce red dragon', error: null, result: null }
        : { ok: true, text: '', error: null, result: { image_url: 'gen.png' } };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(report.succeeded).toEqual(['L', 'G']);
    expect(seen.G.prompt).toBe('a fierce red dragon'); // null result → {text} → piped
  });

  it('image_gen → preview (passive sink) does not crash and skips the sink', async () => {
    const nodes = [node('G', 'image_gen', { prompt: 'a cat' }), node('V', 'preview')];
    const conns = [wire('G', 'image-out', 'V', 'image-in')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: '', error: null, result: { image_url: 'gen.png' } };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(ran).toEqual(['G']); // preview never dispatched
    expect(report.succeeded).toEqual(['G']);
    expect(report.skipped).toContain('V');
    expect(report.failed).toEqual([]);
  });

  it('a piped prompt reaches the llm body derivation (data.prompt → ctx.body)', async () => {
    // The llm has no own prompt/body; a connected prompt node supplies it, and
    // readBody derivation (body||prompt||text) must pick the piped prompt up.
    const nodes = [node('P', 'prompt', { prompt: 'write a poem' }), node('L', 'llm')];
    const conns = [wire('P', 'prompt-out', 'L', 'prompt-in')];
    let seenBody: string | undefined;
    const runner: ClassicRunner = async (ctx) => {
      seenBody = ctx.body;
      return { ok: true, text: 'ok', error: null, result: { text: 'a poem' } };
    };
    const r = recorder();
    await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(seenBody).toBe('write a poem');
  });

  it('a piped prompt reaches the comfy body derivation as well', async () => {
    const nodes = [node('P', 'prompt', { prompt: 'render this' }), node('C', 'comfy')];
    const conns = [wire('P', 'prompt-out', 'C', 'prompt-in')];
    let seenBody: string | undefined;
    const runner: ClassicRunner = async (ctx) => {
      seenBody = ctx.body;
      return { ok: true, text: '', error: null, result: { image_url: 'c.png' } };
    };
    const r = recorder();
    await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(seenBody).toBe('render this');
  });

  it('chains a text source → llm → image_gen, piping llm text into the image prompt', async () => {
    const nodes = [
      node('T', 'text', { text: 'a dragon' }),
      node('L', 'llm'),
      node('G', 'image_gen'),
    ];
    const conns = [
      wire('T', 'text-out', 'L', 'text-in'),
      wire('L', 'text-out', 'G', 'prompt-in'),
    ];
    const seen: Record<string, Record<string, unknown>> = {};
    const runner: ClassicRunner = async (ctx) => {
      seen[ctx.nodeId] = ctx.data;
      return ctx.nodeId === 'L'
        ? { ok: true, text: 'expanded dragon', error: null, result: { text: 'expanded dragon' } }
        : { ok: true, text: '', error: null, result: { image_url: 'd.png' } };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(report.succeeded).toEqual(['L', 'G']);
    expect(seen.L.prompt).toBe('a dragon'); // text-in folded into llm prompt
    expect(seen.G.prompt).toBe('expanded dragon'); // llm output → image prompt
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

// ---------------------------------------------------------------------------
// W3: video source node (passive)
// ---------------------------------------------------------------------------

describe('runClassicCascade — W3 video source node (passive)', () => {
  it('video source is skipped (passive), its video_url pipes into a downstream preview', async () => {
    // V(video, passive source) → P(preview, passive sink). No runnables — just
    // verify the video source is passively recorded and the cascade does not
    // throw or fail.
    const nodes = [
      node('V', 'video', { video_url: 'https://cdn/v.mp4' }),
      node('P', 'preview'),
    ];
    const conns = [wire('V', 'video-out', 'P', 'video-in')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: '', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    // Neither video nor preview dispatches to backend (both passive).
    expect(ran).toEqual([]);
    expect(report.skipped).toContain('V');
    expect(report.skipped).toContain('P');
    expect(report.failed).toEqual([]);
    expect(report.blocked).toEqual([]);
    expect(r.toasts).toEqual([]);
  });

  it('video source alongside a runnable chain is skipped, chain runs', async () => {
    // V(video, passive) isolated; P(prompt) → L(llm, runnable).
    const nodes = [
      node('V', 'video', { video_url: 'v.mp4' }),
      node('P', 'prompt', { prompt: 'hello' }),
      node('L', 'llm'),
    ];
    const conns = [edge('P', 'L')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(ran).toEqual(['L']);
    expect(report.succeeded).toContain('L');
    expect(report.skipped).toContain('V');
    expect(report.failed).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// W3: text_join transform node
// ---------------------------------------------------------------------------

describe('runClassicCascade — W3 text_join transform node', () => {
  it('text_join is skipped (not dispatched to backend), cascade succeeds', async () => {
    const nodes = [
      node('J', 'text_join', { text_a: 'Hello', text_b: 'World' }),
    ];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, [], runner, r.handlers);

    // text_join is a transform — no backend call.
    expect(ran).toEqual([]);
    expect(report.skipped).toContain('J');
    expect(report.failed).toEqual([]);
    expect(report.blocked).toEqual([]);
  });

  it('text_join pipes its computed join into a downstream llm prompt', async () => {
    // T1(text, passive) → J(text_join, transform) → L(llm, runnable).
    // The llm receives the joined text as its prompt.
    const nodes = [
      node('T1', 'text', { text: 'Hello' }),
      node('T2', 'text', { text: 'World' }),
      node('J', 'text_join'),
      node('L', 'llm'),
    ];
    const conns = [
      wire('T1', 'text-out', 'J', 'text-a-in'),
      wire('T2', 'text-out', 'J', 'text-b-in'),
      wire('J', 'text-out', 'L', 'text-in'),
    ];
    let seenData: Record<string, unknown> | undefined;
    const runner: ClassicRunner = async (ctx) => {
      seenData = ctx.data;
      return { ok: true, text: 'ok', error: null, result: { text: 'ok' } };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(report.succeeded).toEqual(['L']);
    // text_join joined 'Hello' + 'World' with a space → llm sees 'Hello World' as prompt
    expect(seenData!.prompt).toBe('Hello World');
  });

  it('text_join with a custom separator joins correctly', async () => {
    const nodes = [
      node('T1', 'text', { text: 'Part A' }),
      node('T2', 'text', { text: 'Part B' }),
      node('J', 'text_join', { separator: '\n' }),
      node('L', 'llm'),
    ];
    const conns = [
      wire('T1', 'text-out', 'J', 'text-a-in'),
      wire('T2', 'text-out', 'J', 'text-b-in'),
      wire('J', 'text-out', 'L', 'text-in'),
    ];
    let seenData: Record<string, unknown> | undefined;
    const runner: ClassicRunner = async (ctx) => {
      seenData = ctx.data;
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(seenData!.prompt).toBe('Part A\nPart B');
  });

  it('text_join with static (non-piped) text_a and text_b uses own data directly', async () => {
    // J has no incoming wires; its static data.text_a + text_b are used.
    const nodes = [
      node('J', 'text_join', { text_a: 'Static A', text_b: 'Static B' }),
      node('L', 'llm'),
    ];
    const conns = [wire('J', 'text-out', 'L', 'text-in')];
    let seenData: Record<string, unknown> | undefined;
    const runner: ClassicRunner = async (ctx) => {
      seenData = ctx.data;
      return { ok: true, text: 'ok', error: null };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(report.succeeded).toContain('L');
    expect(seenData!.prompt).toBe('Static A Static B');
  });

  it('text_join does NOT mutate the persisted node data (immutability)', async () => {
    const jNode = node('J', 'text_join', { text_a: 'own' });
    const t2 = node('T2', 'text', { text: 'piped' });
    const nodes = [t2, jNode, node('L', 'llm')];
    const conns = [
      wire('T2', 'text-out', 'J', 'text-b-in'),
      wire('J', 'text-out', 'L', 'text-in'),
    ];
    const runner: ClassicRunner = async () => ({ ok: true, text: 'ok', error: null });
    const r = recorder();
    await runClassicCascade(nodes, conns, runner, r.handlers);

    // The stored node data is untouched — transform built a per-run copy.
    expect((jNode.data as Record<string, unknown>).text_b).toBeUndefined();
  });

  it('text_join failing downstream does not dispatch text_join (transform is skipped)', async () => {
    // J(text_join) → L(llm, fails). J still not dispatched; L fails normally.
    const nodes = [
      node('J', 'text_join', { text_a: 'A', text_b: 'B' }),
      node('L', 'llm'),
    ];
    const conns = [wire('J', 'text-out', 'L', 'text-in')];
    const ran: string[] = [];
    const runner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: false, text: '', error: 'llm down' };
    };
    const r = recorder();
    const report = await runClassicCascade(nodes, conns, runner, r.handlers);

    expect(ran).toEqual(['L']); // only llm dispatched, J is a transform (skipped)
    expect(report.skipped).toContain('J');
    expect(report.failed).toContain('L');
  });
});
