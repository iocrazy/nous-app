// features/canvas-core/smart/clipRun.ts
//
// M1 MiniMax workbench — per-clip generation (IC's Generate clip): ONE
// video task for one segment, using the clip's own prompt and reference
// frame (falling back to the timeline's wired first input). The result
// lands on that segment (result_url) for the in-node player; the whole-film
// Run (timelineRun) stays untouched. Clip seconds are NOT sent — the plain
// video-generation channel has no duration knob (the whole-film run keeps
// honoring them); the provider default applies for single clips.

import {
  dispatchGenerations,
  pollGeneration,
} from '../services/canvasGenerationService';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { resolveSourceUrls } from './promptInputs';
import { setSegmentResult, type TimelineNodeData } from './timeline';
import type { CanvasConnection, CanvasNode } from '../types';

export interface ClipRunResult {
  ok: boolean;
  error?: string;
}

const inFlight = new Set<string>();

function nodeData(nodeId: string): TimelineNodeData | undefined {
  return (useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id?: unknown }).id === nodeId) as
    | { data?: TimelineNodeData }
    | undefined)?.data;
}

function patchSegments(nodeId: string, mutate: (d: TimelineNodeData) => Partial<TimelineNodeData>): void {
  const data = nodeData(nodeId);
  if (!data) return;
  useCanvasCoreStore.getState().patchNode(nodeId, { data: mutate(data) });
}

export async function runSegmentClip(
  nodeId: string,
  segmentId: string,
): Promise<ClipRunResult> {
  const key = `${nodeId}:${segmentId}`;
  if (inFlight.has(key)) return { ok: false, error: 'clip already running' };
  const { canvasId, nodes, connections } = useCanvasCoreStore.getState();
  if (!canvasId) return { ok: false, error: 'no open canvas' };
  const data = nodeData(nodeId);
  const seg = data?.segments?.find((s) => s.id === segmentId);
  if (!seg) return { ok: false, error: 'segment not found' };
  if (!seg.prompt.trim()) return { ok: false, error: 'clip prompt is empty' };

  const ref =
    seg.ref_url ??
    resolveSourceUrls(nodeId, nodes as CanvasNode[], connections as CanvasConnection[])[0] ??
    null;

  inFlight.add(key);
  try {
    const [taskId] = await dispatchGenerations(canvasId, {
      node_id: nodeId,
      kind: 'video',
      prompt: seg.prompt,
      model: data?.model ?? '',
      count: 1,
      params: { aspect: data?.aspect ?? '16:9' },
      ...(ref ? { source_url: ref } : {}),
    });
    const task = await pollGeneration(taskId, { intervalMs: 2500 });
    const url = ('metadata' in task ? task.metadata?.result_url : undefined) as
      | string
      | undefined;
    if (task.phase !== 'completed' || !url) {
      return {
        ok: false,
        error:
          ('error_msg' in task ? task.error_msg : '') ||
          `clip generation ${task.phase}`,
      };
    }
    patchSegments(nodeId, (d) => ({
      segments: setSegmentResult(d.segments ?? [], segmentId, url),
    }));
    return { ok: true };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : 'clip generation failed',
    };
  } finally {
    inFlight.delete(key);
  }
}
