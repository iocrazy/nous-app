/**
 * useMentionBadges — subscribes to real-time UPDATE events on
 * `conversation_members` filtered to the current user, firing `onChange`
 * whenever the server bumps (or resets) `mention_count` for any conversation
 * the user belongs to.
 *
 * Mirrors the ref + lifecycle + null-guard pattern of useConversationRealtime.
 */

import { useEffect, useRef } from 'react';
import { getSupabaseClient } from '../supabaseClient';

export function useMentionBadges(
  userId: string | null,
  onChange: (channelId: string, mentionCount: number) => void,
): void {
  // Stable ref so the subscription closure always calls the latest callback
  // without needing to be recreated whenever the parent re-renders.
  const cbRef = useRef(onChange);
  cbRef.current = onChange;

  useEffect(() => {
    if (!userId) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    const channel = supabase
      .channel(`mention-badges-${userId}`)
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'conversation_members',
          filter: `user_id=eq.${userId}`,
        },
        (payload) => {
          const row = payload.new as { conversation_id: unknown; mention_count: unknown };
          cbRef.current(
            String(row.conversation_id),
            typeof row.mention_count === 'number' ? row.mention_count : 0,
          );
        },
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [userId]);
}
