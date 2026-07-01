import { useEffect, useRef } from 'react';
import { getSupabaseClient } from '../supabaseClient';
import {
  normalizeConversationMessage,
  type ConversationMessageRow,
} from '../services/conversationService';
import type { ChatMessage } from '../types';

export function useConversationRealtime(
  conversationId: string | null,
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
    if (!conversationId) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    // First SUBSCRIBED arms the flag; any later SUBSCRIBED (emitted after
    // Supabase auto-rejoins from CHANNEL_ERROR/TIMED_OUT/CLOSED) is a reconnect.
    let hasSubscribed = false;

    const channel = supabase
      .channel(`conv-${conversationId}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'messages', filter: `conversation_id=eq.${conversationId}` },
        (payload) =>
          cbRef.current(normalizeConversationMessage(payload.new as ConversationMessageRow)),
      )
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'messages', filter: `conversation_id=eq.${conversationId}` },
        (payload) =>
          updateCbRef.current?.(
            normalizeConversationMessage(payload.new as ConversationMessageRow),
          ),
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
  }, [conversationId]);
}
