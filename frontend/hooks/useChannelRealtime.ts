import { useEffect, useRef } from 'react';
import { getSupabaseClient } from '../supabaseClient';
import type { ChatMessage } from '../types';

export function useChannelRealtime(
  channelId: string | null,
  onInsert: (m: ChatMessage) => void,
) {
  const cbRef = useRef(onInsert);
  cbRef.current = onInsert;

  useEffect(() => {
    if (!channelId) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    const channel = supabase
      .channel(`chat-${channelId}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'channel_messages', filter: `channel_id=eq.${channelId}` },
        (payload) => cbRef.current(payload.new as ChatMessage),
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [channelId]);
}
