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
 * Coordinate contract (xyflow 0.0.76): on the INVALID branch — which is exactly
 * where we run, after the `isValid` early-return — `connectionState.to` is
 * pane-relative SCREEN pixels, not flow coordinates. Using it against flow-space
 * ports or as a node position is only correct at pan=0/zoom=1 (and autoPan makes
 * even that transient). So we derive the release point from the raw pointer
 * event and convert it through the injected `toFlowPosition`.
 *
 * Store-agnostic: candidate ports, the flow-space conversion, and the
 * connect/create actions are all injected, so both the script editor and
 * canvas-core reuse it.
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
  /** Candidate target ports in flow space. The origin node is excluded here. */
  getPorts: () => SnapPort[];
  /** Convert a viewport SCREEN point (clientX/clientY) to flow space. */
  toFlowPosition: (screenPoint: { x: number; y: number }) => { x: number; y: number };
  /** Called when the release snaps to an existing port. */
  onMagneticConnect: (args: MagneticConnectArgs) => void;
  /** Called when the release lands in empty canvas. */
  onOpenCreateMenu: (ctx: DragToCreateContext) => void;
  /**
   * Optional legality gate for a magnetic snap. When it returns false, the
   * snap is rejected and the gesture falls through to the create menu instead
   * of being silently dropped.
   */
  isValidTarget?: (args: MagneticConnectArgs) => boolean;
  /** Magnetic radius, flow-space px. Defaults to portSnap's 48. */
  snapRadius?: number;
}

export interface DragToCreateHandlers {
  onConnectStart: OnConnectStart;
  onConnectEnd: OnConnectEnd;
}

/** Screen (client) coordinates of the pointer that ended the drag. */
function clientPointOf(evt: MouseEvent | TouchEvent): { x: number; y: number } | null {
  if ('clientX' in evt) return { x: evt.clientX, y: evt.clientY };
  const touch = evt.changedTouches?.[0] ?? evt.touches?.[0];
  return touch ? { x: touch.clientX, y: touch.clientY } : null;
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

  const onConnectEnd = useCallback<OnConnectEnd>((evt, connectionState) => {
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

    // Release point → flow space (see the coordinate contract note above).
    const client = clientPointOf(evt);
    if (!client) return;
    const flow = optsRef.current.toFlowPosition(client);

    // Candidate ports exclude the origin node so a wire never snaps back onto
    // its own source node (which would create a self-loop edge).
    const ports = optsRef.current.getPorts().filter((p) => p.nodeId !== from.nodeId);
    const hit = nearestPort(flow, ports, optsRef.current.snapRadius);
    if (hit) {
      const args: MagneticConnectArgs = {
        fromNodeId: from.nodeId,
        fromHandle: from.handleId,
        toNodeId: hit.nodeId,
        toHandle: hit.id,
      };
      // Snap only completes when the connection is legal; an illegal snap
      // falls through to the create menu rather than vanishing with no feedback.
      if (optsRef.current.isValidTarget?.(args) ?? true) {
        optsRef.current.onMagneticConnect(args);
        return;
      }
    }
    optsRef.current.onOpenCreateMenu({
      flowPosition: flow,
      fromNodeId: from.nodeId,
      fromHandle: from.handleId,
    });
  }, []);

  return { onConnectStart, onConnectEnd };
}
