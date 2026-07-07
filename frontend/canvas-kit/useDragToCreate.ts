/**
 * useDragToCreate — wire-drag-to-create for a React Flow surface (canvas-kit,
 * clean-room). Wraps xyflow's onConnectStart/onConnectEnd:
 *
 *   • Drag a wire from a node's OUTPUT handle and release it…
 *       – within `snapRadius` (48px) of a candidate port → magnetic connect;
 *       – on empty canvas → open a create menu at the drop point, carrying the
 *         origin node/handle so the caller can spawn a node and auto-wire it.
 *   • Escape mid-drag cancels — no connect, no menu.
 *   • A release that xyflow itself judged valid (dropped squarely on a handle
 *     within its own radius) is left alone: xyflow's onConnect already committed.
 *
 * Store-agnostic: candidate ports and the connect/create actions are injected,
 * so both the script editor and canvas-core could reuse it.
 */
import { useCallback, useEffect, useRef } from 'react';
import type { OnConnectStart, OnConnectEnd } from '@xyflow/react';

import { nearestPort, type SnapPort } from './portSnap';

export interface DragToCreateContext {
  /** Flow-space position where the wire was released. */
  flowPosition: { x: number; y: number };
  fromNodeId: string;
  fromHandle: string | null;
}

export interface MagneticConnectArgs {
  fromNodeId: string;
  fromHandle: string | null;
  toNodeId: string;
  toHandle: string | null;
}

export interface UseDragToCreateOptions {
  /** Candidate target ports in flow space (caller excludes the origin node). */
  getPorts: () => SnapPort[];
  /** Called when the release snaps to an existing port. */
  onMagneticConnect: (args: MagneticConnectArgs) => void;
  /** Called when the release lands in empty canvas. */
  onOpenCreateMenu: (ctx: DragToCreateContext) => void;
  /** Magnetic radius, flow-space px. Defaults to portSnap's 48. */
  snapRadius?: number;
}

export interface DragToCreateHandlers {
  onConnectStart: OnConnectStart;
  onConnectEnd: OnConnectEnd;
}

export function useDragToCreate(opts: UseDragToCreateOptions): DragToCreateHandlers {
  // Latest options without re-binding the handlers each render.
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const fromRef = useRef<{ nodeId: string; handleId: string | null } | null>(null);
  const cancelledRef = useRef(false);

  // Escape cancels an in-flight drag before it can connect or open the menu.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && fromRef.current) cancelledRef.current = true;
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const onConnectStart = useCallback<OnConnectStart>((_evt, params) => {
    // Only a drag that STARTS from a source (output) handle creates downstream.
    if (params.handleType !== 'source' || !params.nodeId) {
      fromRef.current = null;
      return;
    }
    fromRef.current = { nodeId: params.nodeId, handleId: params.handleId ?? null };
    cancelledRef.current = false;
  }, []);

  const onConnectEnd = useCallback<OnConnectEnd>((_evt, connectionState) => {
    const from = fromRef.current;
    fromRef.current = null;
    if (!from) return;
    if (cancelledRef.current) {
      cancelledRef.current = false;
      return;
    }
    // Dropped squarely on a valid handle → xyflow's own onConnect already
    // committed the edge; nothing to add here.
    if (connectionState.isValid) return;
    const to = connectionState.to;
    if (!to) return;

    const hit = nearestPort({ x: to.x, y: to.y }, optsRef.current.getPorts(), optsRef.current.snapRadius);
    if (hit) {
      optsRef.current.onMagneticConnect({
        fromNodeId: from.nodeId,
        fromHandle: from.handleId,
        toNodeId: hit.nodeId,
        toHandle: hit.id,
      });
      return;
    }
    optsRef.current.onOpenCreateMenu({
      flowPosition: { x: to.x, y: to.y },
      fromNodeId: from.nodeId,
      fromHandle: from.handleId,
    });
  }, []);

  return { onConnectStart, onConnectEnd };
}
