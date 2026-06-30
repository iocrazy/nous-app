import { getAuthHeaders } from './parserService';
import type { Channel, ChatMessage } from '../types';

const API_BASE =
  ('VITE_API_URL' in import.meta.env ? import.meta.env.VITE_API_URL : '') ||
  'http://localhost:8080';

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = await getAuthHeaders();
  const resp = await fetch(`${API_BASE}/api/v1${path}`, { ...init, headers });
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      detail = (await resp.json())?.detail ?? detail;
    } catch (err) {
      console.error('[conversationService] error body parse failed', err);
    }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

// Minimal Conversation type. The new API exposes scope_id / last_seq instead
// of the legacy team_id / last_message_seq. toConversation normalises these
// back to the Channel shape so ChatPage state stays typed as Channel[].
type ConversationRow = Omit<Channel, 'mentions' | 'id' | 'team_id' | 'last_message_seq'> & {
  id: string | number;
  scope_id: string | number;
  last_seq: string | number;
  mention_count?: number;
  mentions?: number;
};

// Map the new API field names (scope_id / last_seq) onto the Channel interface
// (team_id / last_message_seq) and coerce snowflake BIGINTs to string so
// comparisons like `ch.team_id === selectedTeamId` keep working.
const toConversation = (r: ConversationRow): Channel => ({
  ...r,
  id: String(r.id),
  team_id: String(r.scope_id),
  last_message_seq: String(r.last_seq),
  mentions: r.mention_count ?? r.mentions ?? 0,
});

export const conversationService = {
  listChannels: async (): Promise<Channel[]> =>
    (await req<ConversationRow[]>('/conversations')).map(toConversation),

  createChannel: async (p: {
    type: 'group' | 'dm' | 'public';
    team_id: string;
    name?: string | null;
    history_mode?: 'shared' | 'joined';
    member_ids?: string[];
  }): Promise<Channel> =>
    toConversation(await req<ConversationRow>('/conversations', { method: 'POST', body: JSON.stringify(p) })),

  addMembers: (conversationId: string, userIds: string[]) =>
    req<{ added: number }>(`/conversations/${conversationId}/members`, {
      method: 'POST',
      body: JSON.stringify({ user_ids: userIds }),
    }),

  listMessages: (conversationId: string, beforeSeq?: string, limit = 30) => {
    const q = new URLSearchParams();
    if (beforeSeq) q.set('before_seq', beforeSeq);
    q.set('limit', String(limit));
    return req<ChatMessage[]>(`/conversations/${conversationId}/messages?${q.toString()}`);
  },

  sendMessage: (
    conversationId: string,
    body: Record<string, unknown>,
    contentType: 'text' | 'media_card' | 'task_card' = 'text',
    replyToId?: string,
  ) =>
    req<ChatMessage>(`/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ type: contentType, body, parent_id: replyToId ?? null }),
    }),

  markRead: (conversationId: string, lastReadSeq: string) =>
    req<{ ok: boolean }>(`/conversations/${conversationId}/read`, {
      method: 'POST',
      body: JSON.stringify({ last_read_seq: Number(lastReadSeq) }),
    }),

  editMessage: (conversationId: string, messageId: string, body: Record<string, unknown>) =>
    req<ChatMessage>(`/conversations/${conversationId}/messages/${messageId}`, {
      method: 'PATCH',
      body: JSON.stringify({ body }),
    }),

  deleteMessage: (conversationId: string, messageId: string) =>
    req<ChatMessage>(`/conversations/${conversationId}/messages/${messageId}`, {
      method: 'DELETE',
    }),

  addAgent: (conversationId: string, agentSlug: string) =>
    req<{ added: boolean; agent_id?: string }>(`/conversations/${conversationId}/agents`, {
      method: 'POST',
      body: JSON.stringify({ agent_slug: agentSlug }),
    }),
};
