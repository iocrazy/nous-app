/**
 * useCanvasRealtime (Phase 6a — Realtime node-state sync, cross-tab / cross-user).
 *
 * Subscribes to Supabase Realtime postgres_changes UPDATE events on the
 * `canvases` table filtered by the open canvas ID. When another tab or
 * user saves a newer revision the event fires and we call
 * `applyRemoteUpdate` in the store, which decides whether to rebase or
 * surface a conflict — we never render directly from the broadcast payload.
 *
 * Pattern mirrors TaskManagerContext.tsx:590-638 (single channel, cleanup
 * on unmount). No bespoke WebSocket server needed — the canvas row is
 * already in Postgres and the `supabase_realtime` publication includes
 * `canvases` after migration 296.
 */

import { useEffect } from 'react';

import { getSupabaseClient } from '../../../supabaseClient';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { Canvas } from '../types';

export function useCanvasRealtime(canvasId: string | null | undefined): void {
  const applyRemoteUpdate = useCanvasCoreStore((s) => s.applyRemoteUpdate);

  useEffect(() => {
    if (!canvasId) return;

    const supabase = getSupabaseClient();
    if (!supabase) {
      console.warn('[CanvasRealtime] No Supabase client — skipping subscription');
      return;
    }

    const channel = supabase
      .channel(`canvas-realtime-${canvasId}`)
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'canvases',
          filter: `id=eq.${canvasId}`,
        },
        (payload) => {
          // Treat payload.new as a signal: the store guards self-echo,
          // stale timestamps, and dirty-edit conflicts before applying.
          applyRemoteUpdate(payload.new as Canvas);
        },
      )
      .subscribe((status, err) => {
        if (err) {
          console.warn('[CanvasRealtime] Subscription error:', err);
        } else {
          console.debug(`[CanvasRealtime] Realtime status for ${canvasId}: ${status}`);
        }
      });

    return () => {
      supabase.removeChannel(channel);
    };
  }, [canvasId, applyRemoteUpdate]);
}
