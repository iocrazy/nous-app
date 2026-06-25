/**
 * MessageBubble — a single message row in the Team Chat stream.
 *
 * Rendering rules:
 *   - sender_type === 'agent'    → amber gradient avatar + "AGENT" tag
 *   - content_type === 'media_card' → media card from message.body
 *   - otherwise                 → plain text from message.body.text
 *
 * Purely presentational — no data fetching.
 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, Download } from 'lucide-react';
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
    <div className="mt-2 max-w-[420px] bg-[#1d1d22] border border-white/[.12] rounded-[12px] overflow-hidden">
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
          <div className="text-[13.5px] font-semibold text-[#e7e7ea] whitespace-nowrap overflow-hidden text-ellipsis">
            {body.title}
          </div>
        )}
        {body.fields && body.fields.length > 0 && (
          <div className="flex gap-4 mt-[7px]">
            {body.fields.map((f) => (
              <div key={f.title} className="flex flex-col">
                <span className="text-[10px] uppercase tracking-[0.06em] text-[#74747e]">
                  {f.title}
                </span>
                <span className="text-[12.5px] text-[#a3a3ad] mt-px">
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
          className="flex items-center gap-[6px] text-[12px] px-[11px] py-[5px] rounded-[9px] bg-[#17171b] border border-white/[.065] text-[#a3a3ad] transition-colors hover:text-[#e7e7ea] hover:bg-[#1d1d22]"
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
}

export function MessageBubble({ message }: MessageBubbleProps): React.ReactElement {
  const isAgent = message.sender_type === 'agent';
  const isMediaCard = message.content_type === 'media_card';

  const avatarClass = isAgent
    ? 'bg-gradient-to-br from-amber-400 to-amber-500 text-[#1a1505]'
    : 'bg-gradient-to-br from-slate-500 to-slate-400 text-white';

  // Derive display name: body may carry a sender_name hint; fall back to id
  const displayName =
    typeof message.body.sender_name === 'string'
      ? message.body.sender_name
      : (message.sender_id ?? 'Unknown');

  return (
    <div className="flex gap-[11px]">
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
          <span className="text-[13.5px] font-[650] text-[#e7e7ea]">
            {displayName}
          </span>
          {isAgent && (
            <span className="text-[10px] font-semibold text-amber-400 bg-amber-400/10 px-[6px] py-[1.5px] rounded-[5px] tracking-[0.02em]">
              AGENT
            </span>
          )}
          <span className="text-[10.5px] text-[#74747e]">
            {fmtTime(message.created_at)}
          </span>
        </div>

        {/* Content */}
        {isMediaCard ? (
          <MediaCard body={message.body as MediaCardBody} />
        ) : (
          <p className="text-[14px] text-[#e7e7ea] leading-[1.55] whitespace-pre-wrap break-words">
            {String(message.body.text ?? '')}
          </p>
        )}
      </div>
    </div>
  );
}
