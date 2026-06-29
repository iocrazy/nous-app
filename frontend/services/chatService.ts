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
      console.error('[chatService] error body parse failed', err);
    }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

type ChannelRow = Omit<Channel, 'mentions' | 'id' | 'team_id' | 'last_message_seq'> & {
  id: string | number;
  team_id: string | number;
  last_message_seq: string | number;
  mention_count?: number;
  mentions?: number;
};
// The API serializes BIGINT/snowflake columns as JSON numbers, but Channel
// declares them as strings (and callers compare against string ids from the
// URL). Coerce the snowflake fields to string so e.g. `team_id === selectedTeamId`
// matches — without this, channels are filtered out on reload (number !== string).
const toChannel = (r: ChannelRow): Channel => ({
  ...r,
  id: String(r.id),
  team_id: String(r.team_id),
  last_message_seq: String(r.last_message_seq),
  mentions: r.mention_count ?? r.mentions ?? 0,
});

export const chatService = {
  listChannels: async (): Promise<Channel[]> =>
    (await req<ChannelRow[]>('/chat/channels')).map(toChannel),

  createChannel: async (p: {
    type: 'group' | 'dm' | 'public';
    team_id: string;
    name?: string | null;
    history_mode?: 'shared' | 'joined';
    member_ids?: string[];
  }): Promise<Channel> =>
    toChannel(await req<ChannelRow>('/chat/channels', { method: 'POST', body: JSON.stringify(p) })),

  addMembers: (channelId: string, userIds: string[]) =>
    req<{ added: number }>(`/chat/channels/${channelId}/members`, {
      method: 'POST',
      body: JSON.stringify({ user_ids: userIds }),
    }),

  listMessages: (channelId: string, beforeSeq?: string, limit = 30) => {
    const q = new URLSearchParams();
    if (beforeSeq) q.set('before_seq', beforeSeq);
    q.set('limit', String(limit));
    return req<ChatMessage[]>(`/chat/channels/${channelId}/messages?${q.toString()}`);
  },

  sendMessage: (
    channelId: string,
    body: Record<string, unknown>,
    contentType: 'text' | 'media_card' | 'task_card' = 'text',
    replyToId?: string,
  ) =>
    req<ChatMessage>(`/chat/channels/${channelId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content_type: contentType, body, reply_to_id: replyToId ?? null }),
    }),

  markRead: (channelId: string, lastReadSeq: string) =>
    req<{ ok: boolean }>(`/chat/channels/${channelId}/read`, {
      method: 'POST',
      body: JSON.stringify({ last_read_seq: Number(lastReadSeq) }),
    }),

  editMessage: (channelId: string, messageId: string, body: Record<string, unknown>) =>
    req<ChatMessage>(`/chat/channels/${channelId}/messages/${messageId}`, {
      method: 'PATCH',
      body: JSON.stringify({ body }),
    }),

  deleteMessage: (channelId: string, messageId: string) =>
    req<ChatMessage>(`/chat/channels/${channelId}/messages/${messageId}`, {
      method: 'DELETE',
    }),

  addAgent: (channelId: string, agentSlug: string) =>
    req<{ added: boolean; agent_id?: string }>(`/chat/channels/${channelId}/agents`, {
      method: 'POST',
      body: JSON.stringify({ agent_slug: agentSlug }),
    }),
};
