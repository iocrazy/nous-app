import { useEffect, useRef } from 'react';
import { getSupabaseClient } from '../supabaseClient';
import type { ChatMessage } from '../types';

export function useChannelRealtime(
  channelId: string | null,
  onInsert: (m: ChatMessage) => void,
  onUpdate?: (m: ChatMessage) => void,
  onResubscribe?: () => void,
) {
  const cbRef = useRef(onInsert);
  cbRef.current = onInsert;

  const updateCbRef = useRef(onUpdate);
  updateCbRef.current = onUpdate;

  const resubRef = useRef(onResubscribe);
  resubRef.current = onResubscribe;

  useEffect(() => {
    if (!channelId) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    // First SUBSCRIBED arms the flag; any later SUBSCRIBED (emitted after
    // Supabase auto-rejoins from CHANNEL_ERROR/TIMED_OUT/CLOSED) is a reconnect.
    let hasSubscribed = false;

    const channel = supabase
      .channel(`chat-${channelId}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'channel_messages', filter: `channel_id=eq.${channelId}` },
        (payload) => cbRef.current(payload.new as ChatMessage),
      )
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'channel_messages', filter: `channel_id=eq.${channelId}` },
        (payload) => updateCbRef.current?.(payload.new as ChatMessage),
      )
      .subscribe((status) => {
        if (status === 'SUBSCRIBED') {
          if (hasSubscribed) resubRef.current?.();
          else hasSubscribed = true;
        }
      });

    return () => {
      supabase.removeChannel(channel);
    };
  }, [channelId]);
}
