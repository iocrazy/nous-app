import { useEffect, useRef } from 'react';
import type { RealtimeChannel } from '@supabase/supabase-js';
import { getSupabaseClient } from '../../supabaseClient';
import type { RemoteOpRow } from '../useSceneSync';

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface ScriptOpsRealtimeHandlers {
  /** Latest scene ids of the open script — the local second gate (see below). */
  getSceneIds: () => string[];
  /** Route a matched row to the scene's applyRemoteOps. */
  dispatchToScene: (sceneId: string, row: RemoteOpRow) => void;
  /** Called once after SUBSCRIBED so the caller can do a full reconcile pull. */
  onReconcile?: () => void;
}

/**
 * Normalise a Supabase Realtime `postgres_changes` record into a RemoteOpRow.
 *
 * Realtime decodes int8 columns via parseFloat, so `scene_id` arrives as a JS
 * number; we coerce it to a string for map lookups. Current snowflake ids are
 * ~3e14, ~28x below 2^53, so no precision is lost — but Realtime gives us no
 * way to request string encoding (realtime-js convertCell hardcodes int8 →
 * number), so this coercion is the ceiling until ids approach 2^53.
 */
function normalizeRow(record: Record<string, unknown>): RemoteOpRow {
  return {
    scene_id: String(record.scene_id),
    op_seq: typeof record.op_seq === 'number' ? record.op_seq : Number(record.op_seq),
    actor: typeof record.actor === 'string' ? record.actor : String(record.actor ?? ''),
    op_json: (record.op_json ?? null) as RemoteOpRow['op_json'],
  };
}

/**
 * Subscribes to INSERTs on `script_ops` for one open script (Phase B P5 / C2).
 *
 * postgres_changes filters are single-column and scene_id is multi-valued, so
 * we subscribe UNFILTERED and drop rows locally whose scene_id is not in the
 * open script's set (`getSceneIds`). RLS (mig 346) already scopes delivered
 * rows to scripts the user can access, so the local filter is a second gate,
 * not the security boundary. On SUBSCRIBED we trigger one full reconcile so any
 * ops missed before the socket opened are caught.
 *
 * Passing `scriptId === null` (flag off) opens no channel.
 */
export function useScriptOpsRealtime(
  scriptId: string | null,
  handlers: ScriptOpsRealtimeHandlers,
): void {
  // Keep handlers in refs so the channel effect depends only on scriptId and
  // the INSERT callback always reads the freshest scene set / dispatchers.
  const getSceneIdsRef = useRef(handlers.getSceneIds);
  const dispatchToSceneRef = useRef(handlers.dispatchToScene);
  const onReconcileRef = useRef(handlers.onReconcile);
  getSceneIdsRef.current = handlers.getSceneIds;
  dispatchToSceneRef.current = handlers.dispatchToScene;
  onReconcileRef.current = handlers.onReconcile;

  useEffect(() => {
    if (!scriptId) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    let channel: RealtimeChannel | null = null;
    let cancelled = false;

    void (async () => {
      // Apply the session token to the realtime socket BEFORE joining. Supabase
      // freezes a postgres_changes subscription's JWT claims at join time, so
      // subscribing before supabase-js has called realtime.setAuth() evaluates
      // this channel as anon → mig346's can_read_script_op() is false → the
      // server silently never delivers a single row (channel still reads
      // 'joined'). Real-machine canary: a probe channel joined after auth on the
      // same socket got every op; this one got none until we set auth first.
      const { data } = await supabase.auth.getSession();
      const token = data.session?.access_token;
      if (!token || cancelled) return; // no session, or unmounted mid-await
      supabase.realtime.setAuth(token);

      channel = supabase
        .channel('script-ops-' + scriptId)
        .on(
          'postgres_changes',
          { event: 'INSERT', schema: 'public', table: 'script_ops' },
          (payload) => {
            const record = payload.new as Record<string, unknown>;
            if (record == null || record.scene_id == null) return;
            const sceneId = String(record.scene_id);
            // Second gate: only rows for scenes in THIS open script.
            if (!getSceneIdsRef.current().includes(sceneId)) return;
            dispatchToSceneRef.current(sceneId, normalizeRow(record));
          },
        )
        .subscribe((status) => {
          // Full reconcile once connected — catches ops missed pre-subscription.
          if (status === 'SUBSCRIBED') onReconcileRef.current?.();
        });
    })();

    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
  }, [scriptId]);
}
