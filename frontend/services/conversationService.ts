import { getAuthHeaders } from './parserService';
import type { Channel, ChatMessage, ConversationMember } from '../types';

const API_BASE =
  ('VITE_API_URL' in import.meta.env ? import.meta.env.VITE_API_URL : '') ||
  'http://localhost:8080';

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(await getAuthHeaders());
  if (init?.body instanceof FormData) {
    headers.delete('Content-Type');
  }
  if (init?.headers) {
    new Headers(init.headers).forEach((value, key) => {
      headers.set(key, value);
    });
  }
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

type ConversationRow = Omit<Channel, 'mentions' | 'id' | 'team_id' | 'last_message_seq'> & {
  id: string | number;
  scope_id: string | number;
  last_seq: string | number;
  mention_count?: number;
  mentions?: number;
};

const toConversation = (r: ConversationRow): Channel => ({
  ...r,
  id: String(r.id),
  team_id: String(r.scope_id),
  last_message_seq: String(r.last_seq),
  mentions: r.mention_count ?? r.mentions ?? 0,
});

export type ConversationMessageRow = Omit<
  Partial<ChatMessage>,
  'id' | 'channel_id' | 'conversation_id' | 'seq' | 'reply_to_id' | 'parent_id'
> & {
  id: string | number;
  channel_id?: string | number;
  conversation_id?: string | number;
  seq: string | number;
  content_type?: ChatMessage['content_type'];
  type?: ChatMessage['content_type'];
  reply_to_id?: string | number | null;
  parent_id?: string | number | null;
};

export interface ConversationImageUpload {
  id: string;
  mime: string;
  file_size_bytes?: number;
  url: string;
}

export function normalizeConversationMessage(row: ConversationMessageRow): ChatMessage {
  const channelId = row.conversation_id ?? row.channel_id;
  const replyToId = row.parent_id ?? row.reply_to_id ?? null;
  const contentType = row.type ?? row.content_type ?? 'text';

  return {
    ...row,
    id: String(row.id),
    channel_id: channelId == null ? '' : String(channelId),
    conversation_id: channelId == null ? undefined : String(channelId),
    seq: String(row.seq),
    sender_id: row.sender_id == null ? null : String(row.sender_id),
    sender_type: row.sender_type ?? 'user',
    content_type: contentType,
    type: contentType,
    body: row.body ?? {},
    reply_to_id: replyToId == null ? null : String(replyToId),
    parent_id: replyToId == null ? null : String(replyToId),
    edited_at: row.edited_at ?? null,
    deleted_at: row.deleted_at ?? null,
    created_at: row.created_at ?? new Date(0).toISOString(),
  };
}

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
    toConversation(await req<ConversationRow>('/conversations', {
      method: 'POST',
      body: JSON.stringify({
        type: p.type,
        scope_id: p.team_id,
        name: p.name ?? null,
        history_mode: p.history_mode ?? 'shared',
        member_ids: p.member_ids ?? [],
      }),
    })),

  addMembers: (conversationId: string, userIds: string[]) =>
    req<{ added: number }>(`/conversations/${conversationId}/members`, {
      method: 'POST',
      body: JSON.stringify({ user_ids: userIds }),
    }),

  listMessages: (conversationId: string, beforeSeq?: string, limit = 30) => {
    const q = new URLSearchParams();
    if (beforeSeq) q.set('before_seq', beforeSeq);
    q.set('limit', String(limit));
    return req<ConversationMessageRow[]>(
      `/conversations/${conversationId}/messages?${q.toString()}`,
    ).then((rows) => rows.map(normalizeConversationMessage));
  },

  sendMessage: (
    conversationId: string,
    body: Record<string, unknown>,
    contentType: 'text' | 'image' | 'media_card' | 'task_card' = 'text',
    replyToId?: string,
  ) =>
    req<ConversationMessageRow>(`/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ type: contentType, body, parent_id: replyToId ?? null }),
    }).then(normalizeConversationMessage),

  markRead: (conversationId: string, lastReadSeq: string) =>
    req<{ ok: boolean }>(`/conversations/${conversationId}/read`, {
      method: 'POST',
      body: JSON.stringify({ last_read_seq: Number(lastReadSeq) }),
    }),

  editMessage: (conversationId: string, messageId: string, body: Record<string, unknown>) =>
    req<ConversationMessageRow>(`/conversations/${conversationId}/messages/${messageId}`, {
      method: 'PATCH',
      body: JSON.stringify({ body }),
    }).then(normalizeConversationMessage),

  deleteMessage: (conversationId: string, messageId: string) =>
    req<ConversationMessageRow>(`/conversations/${conversationId}/messages/${messageId}`, {
      method: 'DELETE',
    }).then(normalizeConversationMessage),

  addAgent: (conversationId: string, agentSlug: string) =>
    req<{ added: boolean; agent_id?: string }>(`/conversations/${conversationId}/agents`, {
      method: 'POST',
      body: JSON.stringify({ agent_slug: agentSlug }),
    }),

  uploadConversationImage: (conversationId: string, file: File) => {
    const body = new FormData();
    body.append('file', file);
    return req<ConversationImageUpload>(`/conversations/${conversationId}/attachments`, {
      method: 'POST',
      body,
    }).then((row) => ({ ...row, id: String(row.id) }));
  },

  saveImageToLibrary: (attachmentId: string, scopeId: string) =>
    req<{ promoted_resource_id: string }>(
      `/conversations/attachments/${attachmentId}/promote`,
      {
        method: 'POST',
        body: JSON.stringify({ scope_id: Number(scopeId) }),
      },
    ).then((row) => ({
      promoted_resource_id: String(row.promoted_resource_id),
    })),

  // ── Group management (owner / admin / member roles) ─────────────────────

  listMembers: (conversationId: string) =>
    req<ConversationMember[]>(`/conversations/${conversationId}/members`),

  removeMember: (conversationId: string, userId: string) =>
    req<{ removed: boolean }>(
      `/conversations/${conversationId}/members/${userId}`,
      { method: 'DELETE' },
    ),

  setMemberRole: (conversationId: string, userId: string, role: 'admin' | 'member') =>
    req<{ updated: boolean; role: string }>(
      `/conversations/${conversationId}/members/${userId}/role`,
      { method: 'PATCH', body: JSON.stringify({ role }) },
    ),

  transferOwner: (conversationId: string, toUserId: string) =>
    req<{ transferred: boolean }>(
      `/conversations/${conversationId}/transfer-owner`,
      { method: 'POST', body: JSON.stringify({ to_user_id: toUserId }) },
    ),

  removeAgent: (conversationId: string, agentId: string) =>
    req<{ removed: boolean }>(
      `/conversations/${conversationId}/agents/${agentId}`,
      { method: 'DELETE' },
    ),

  updateChannel: async (
    conversationId: string,
    patch: { name?: string; type?: 'group' | 'public' },
  ): Promise<Channel> =>
    toConversation(
      await req<ConversationRow>(`/conversations/${conversationId}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      }),
    ),

  dissolveChannel: (conversationId: string) =>
    req<{ archived: boolean }>(`/conversations/${conversationId}`, {
      method: 'DELETE',
    }),
};
