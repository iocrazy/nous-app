/**
 * MessageList — scrollable container for Team Chat messages.
 *
 * - Renders messages oldest → newest (messages[] arrives oldest-last = newest-last).
 * - Shows a "Load older" button at the top when hasOlder === true.
 * - Auto-scrolls to the bottom when the last message id changes.
 *
 * Purely presentational — no data fetching.
 */

import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { MessageBubble } from './MessageBubble';
import type { ChatMessage } from '../../types';

export interface MessageListProps {
  messages: ChatMessage[];
  onLoadOlder: () => void;
  hasOlder: boolean;
  loadingOlder: boolean;
  /** The authenticated user's id for edit/delete ownership checks. */
  currentUserId?: string | null;
  /** Resolves a sender_id (UUID) to a display name for the message bubbles. */
  memberNameById?: Record<string, string>;
  onEdit?: (messageId: string, text: string) => void;
  onDelete?: (messageId: string) => void;
  onSaveImage?: (generatedMediaId: string, scope: 'team' | 'personal') => void;
  canSaveImageToTeam?: boolean;
  canSaveImageToPersonal?: boolean;
}

export function MessageList({
  messages,
  onLoadOlder,
  hasOlder,
  loadingOlder,
  currentUserId,
  memberNameById,
  onEdit,
  onDelete,
  onSaveImage,
  canSaveImageToTeam,
  canSaveImageToPersonal,
}: MessageListProps): React.ReactElement {
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);

  // Track the id of the last message to trigger auto-scroll only on new messages
  const lastId = messages.length > 0 ? messages[messages.length - 1].id : null;

  useEffect(() => {
    if (lastId !== null) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [lastId]);

  return (
    <div className="flex-1 overflow-y-auto px-[22px] py-5 flex flex-col gap-[18px] scrollbar-thin scrollbar-thumb-white/[.08]">
      {/* Load older button */}
      {hasOlder && (
        <div className="flex justify-center flex-shrink-0">
          <button
            type="button"
            onClick={onLoadOlder}
            disabled={loadingOlder}
            className="flex items-center gap-2 text-[12px] text-content-3 hover:text-content-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors px-3 py-1.5 rounded-[9px] bg-island-2 border border-line"
          >
            {loadingOlder && (
              <Loader2 size={12} className="animate-spin" />
            )}
            {t('chat.loadOlder')}
          </button>
        </div>
      )}

      {/* Messages — oldest first */}
      {messages.map((msg) => (
        <MessageBubble
          key={msg.id}
          message={msg}
          currentUserId={currentUserId}
          memberNameById={memberNameById}
          onEdit={onEdit}
          onDelete={onDelete}
          onSaveImage={onSaveImage}
          canSaveImageToTeam={canSaveImageToTeam}
          canSaveImageToPersonal={canSaveImageToPersonal}
        />
      ))}

      {/* Scroll anchor */}
      <div ref={bottomRef} className="h-0 flex-shrink-0" />
    </div>
  );
}
