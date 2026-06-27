/**
 * MessageBubble — a single message row in the Team Chat stream.
 *
 * Rendering rules:
 *   - sender_type === 'agent'    → amber gradient avatar + "AGENT" tag
 *   - content_type === 'media_card' → media card from message.body
 *   - otherwise                 → plain text from message.body.text
 *   - isDeleted (deleted_at set) → tombstone (muted italic); no edit/delete actions
 *
 * Owner gate: edit + delete actions only shown when isOwn && !isDeleted.
 * Inline edit: Enter saves, Shift+Enter inserts newline, Esc cancels.
 *
 * Purely presentational — no data fetching.
 */

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, Download, Pencil, Trash2 } from 'lucide-react';
import type { ChatMessage } from '../../types';

// ── helpers ──────────────────────────────────────────────────────────────────

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}

/** Derive two-letter initials from sender_id (UUID/snowflake) or a name. */
function initials(senderId: string | null): string {
  if (!senderId) return '?';
  // If it looks like a UUID or snowflake, fall back to first 2 chars
  return senderId.slice(0, 2).toUpperCase();
}

// ── Media card sub-component ─────────────────────────────────────────────────

interface MediaCardBody {
  title?: string;
  image_url?: string;
  fields?: { title: string; value: string }[];
}

function MediaCard({ body }: { body: MediaCardBody }): React.ReactElement {
  const { t } = useTranslation();
  const hasThumb = Boolean(body.image_url);

  return (
    <div className="mt-2 max-w-[420px] bg-card border border-line-strong rounded-[12px] overflow-hidden">
      {/* Thumbnail */}
      <div className="h-[150px] bg-gradient-to-br from-slate-800 to-slate-900 relative grid place-items-center">
        {hasThumb ? (
          <img
            src={body.image_url}
            alt={body.title ?? ''}
            className="absolute inset-0 w-full h-full object-cover"
          />
        ) : (
          /* Placeholder play button */
          <div className="w-[46px] h-[46px] rounded-full bg-black/55 border border-white/50 grid place-items-center text-white">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" stroke="none">
              <polygon points="6 3 20 12 6 21 6 3" />
            </svg>
          </div>
        )}
      </div>

      {/* Body */}
      <div className="px-[13px] py-[11px]">
        {body.title && (
          <div className="text-[13.5px] font-semibold text-content whitespace-nowrap overflow-hidden text-ellipsis">
            {body.title}
          </div>
        )}
        {body.fields && body.fields.length > 0 && (
          <div className="flex gap-4 mt-[7px]">
            {body.fields.map((f) => (
              <div key={f.title} className="flex flex-col">
                <span className="text-[10px] uppercase tracking-[0.06em] text-content-3">
                  {f.title}
                </span>
                <span className="text-[12.5px] text-content-2 mt-px">
                  {f.value}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Footer actions */}
      <div className="flex gap-2 px-[13px] pb-3">
        <button
          type="button"
          className="flex items-center gap-[6px] text-[12px] px-[11px] py-[5px] rounded-[9px] bg-indigo-500/[.12] border border-indigo-500/[.35] text-indigo-300 transition-colors hover:bg-indigo-500/[.2]"
        >
          <ExternalLink size={13} />
          {t('chat.openInLibrary')}
        </button>
        <button
          type="button"
          className="flex items-center gap-[6px] text-[12px] px-[11px] py-[5px] rounded-[9px] bg-island-2 border border-line text-content-2 transition-colors hover:text-content hover:bg-card"
        >
          <Download size={13} />
          {t('chat.download')}
        </button>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export interface MessageBubbleProps {
  message: ChatMessage;
  /** Authenticated user id — gates edit/delete actions to own messages. */
  currentUserId?: string | null;
  /** Called when the user saves an edited draft. */
  onEdit?: (id: string, text: string) => void;
  /** Called when the user confirms a soft-delete. */
  onDelete?: (id: string) => void;
}

export function MessageBubble({
  message,
  currentUserId,
  onEdit,
  onDelete,
}: MessageBubbleProps): React.ReactElement {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  const isAgent = message.sender_type === 'agent';
  const isMediaCard = message.content_type === 'media_card';
  const isText = message.content_type === 'text';
  const isOwn = message.sender_type === 'user' && message.sender_id === currentUserId;
  const isDeleted = !!message.deleted_at;

  const avatarClass = isAgent
    ? 'bg-gradient-to-br from-amber-400 to-amber-500 text-[#1a1505]'
    : 'bg-gradient-to-br from-slate-500 to-slate-400 text-white';

  // Derive display name: body may carry a sender_name hint; fall back to id
  const displayName =
    typeof message.body.sender_name === 'string'
      ? message.body.sender_name
      : (message.sender_id ?? 'Unknown');

  // ── event handlers ──────────────────────────────────────────────────────

  function handleEditClick() {
    setDraft(String(message.body.text ?? ''));
    setEditing(true);
  }

  function handleSave() {
    const trimmed = draft.trim();
    const original = String(message.body.text ?? '');
    if (trimmed && trimmed !== original) {
      onEdit?.(message.id, trimmed);
    }
    setEditing(false);
    setDraft('');
  }

  function handleCancel() {
    setEditing(false);
    setDraft('');
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSave();
    } else if (e.key === 'Escape') {
      handleCancel();
    }
    // Shift+Enter: default newline behavior (no override needed)
  }

  function handleDeleteClick() {
    if (window.confirm(t('chat.confirmDelete'))) {
      onDelete?.(message.id);
    }
  }

  // ── render ──────────────────────────────────────────────────────────────

  return (
    <div className="flex gap-[11px] group">
      {/* Avatar */}
      <div
        className={[
          'w-[34px] h-[34px] rounded-[10px] flex-shrink-0 text-[12px] grid place-items-center font-semibold',
          avatarClass,
        ].join(' ')}
        aria-hidden="true"
      >
        {initials(message.sender_id)}
      </div>

      {/* Body */}
      <div className="flex-1 min-w-0">
        {/* Meta row */}
        <div className="flex items-center gap-2 mb-[3px]">
          <span className="text-[13.5px] font-[650] text-content">
            {displayName}
          </span>
          {isAgent && (
            <span className="text-[10px] font-semibold text-amber-400 bg-amber-400/10 px-[6px] py-[1.5px] rounded-[5px] tracking-[0.02em]">
              AGENT
            </span>
          )}
          <span className="text-[10.5px] text-content-3">
            {fmtTime(message.created_at)}
          </span>
          {message.edited_at && !isDeleted && (
            <span className="text-[10.5px] text-content-3">
              {'·'} {t('chat.edited')}
            </span>
          )}

          {/* Hover action buttons — own non-deleted messages only */}
          {isOwn && !isDeleted && (
            <div className="ml-auto flex gap-[4px] opacity-0 group-hover:opacity-100 transition-opacity">
              {isText && (
                <button
                  type="button"
                  onClick={handleEditClick}
                  title={t('chat.edit')}
                  className="p-[5px] rounded-[7px] bg-card border border-line-strong text-content-3 hover:text-content hover:bg-island-2 transition-colors"
                >
                  <Pencil size={12} />
                </button>
              )}
              <button
                type="button"
                onClick={handleDeleteClick}
                title={t('chat.delete')}
                className="p-[5px] rounded-[7px] bg-card border border-line-strong text-content-3 hover:text-red-400 hover:bg-red-500/[.1] transition-colors"
              >
                <Trash2 size={12} />
              </button>
            </div>
          )}
        </div>

        {/* Content */}
        {isDeleted ? (
          /* Tombstone: replace body with muted italic line */
          <p className="text-[13px] text-content-3 italic">
            {t('chat.deleted')}
          </p>
        ) : editing ? (
          /* Inline edit mode */
          <div className="flex flex-col gap-[8px]">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              autoFocus
              rows={3}
              className="w-full bg-card border border-line-strong rounded-[9px] text-[14px] text-content px-[10px] py-[8px] resize-none leading-[1.55] focus:outline-none focus:border-indigo-500/50 transition-colors"
            />
            <div className="flex gap-[6px]">
              <button
                type="button"
                onClick={handleSave}
                className="text-[12px] px-[10px] py-[4px] rounded-[7px] bg-indigo-500/[.18] border border-indigo-500/[.4] text-indigo-300 hover:bg-indigo-500/[.28] transition-colors"
              >
                {t('chat.save')}
              </button>
              <button
                type="button"
                onClick={handleCancel}
                className="text-[12px] px-[10px] py-[4px] rounded-[7px] bg-island-2 border border-line text-content-3 hover:text-content transition-colors"
              >
                {t('chat.cancel')}
              </button>
            </div>
          </div>
        ) : isMediaCard ? (
          <MediaCard body={message.body as MediaCardBody} />
        ) : (
          <p className="text-[14px] text-content leading-[1.55] whitespace-pre-wrap break-words">
            {String(message.body.text ?? '')}
          </p>
        )}
      </div>
    </div>
  );
}
