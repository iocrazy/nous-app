import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import DOMPurify from 'dompurify';
import { Copy, Check, FileText } from 'lucide-react';

import type { AIChatMessageAttachment, ChatToolCall } from '../../types';
import { getResourceCoverUrl } from '../../services/resourceService';
import { ApprovalCard, type AwaitingApproval } from './ApprovalCard';
import { SubTaskList } from './SubTaskCard';
import { CapabilityDeniedNotice } from '../agentActivity/CapabilityDeniedNotice';
import { ToolActivityChips } from '../agentActivity/ToolActivityChips';
import { TurnWriteSummary } from '../agentActivity/TurnWriteSummary';
import {
  fromChatToolCalls,
  isScreenwritingTool,
  summarizeWrites,
} from '../agentActivity/toolActivity';
import { useRunToolActivity } from '../agentActivity/useRunToolActivity';

export interface MessageBubbleProps {
  role: 'user' | 'assistant';
  content: string;
  agentName?: string;
  tokens?: number;
  onApply?: () => void;
  onCopy?: () => void;
  timestamp?: string;
  /**
   * Attachments sent with this turn (images render inline; other kinds
   * render as a filename chip). Persisted server-side so history reloads
   * keep them; the optimistic bubble uses the local preview data URL.
   */
  attachments?: AIChatMessageAttachment[];
  /**
   * Sub-task dispatches the LLM made for this assistant turn. Rendered
   * as collapsible cards above the prose body. Ignored on user bubbles.
   */
  toolCalls?: ChatToolCall[];
  /**
   * Plan Mode (Phase 4.5): the turn paused on a hook's await_approval.
   * Renders an inline Approve/Reject card above the prose body.
   */
  awaitingApproval?: AwaitingApproval;
  /** The agent_runs id that backed this turn (metadata_json.run_id). Threaded
   *  to TurnWriteSummary so its Undo button knows what to undo. */
  runId?: string | null;
}

/** Image strip + file chips shown above the message text. resource_ref
    attachments are skipped — the @-mention is already part of the text. */
function AttachmentStrip({
  attachments,
}: {
  attachments: AIChatMessageAttachment[];
}): React.ReactElement | null {
  const visible = attachments.filter((a) => a.kind !== 'resource_ref');
  if (visible.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mb-1.5">
      {visible.map((att, i) => {
        const src =
          att.preview_data_url ??
          (att.kind === 'image' && att.resource_id
            ? getResourceCoverUrl(String(att.resource_id))
            : undefined);
        if (src) {
          return (
            <img
              key={`${att.resource_id ?? att.alt_text ?? 'att'}-${i}`}
              src={src}
              alt={att.alt_text ?? 'attachment'}
              loading="lazy"
              className="max-h-40 max-w-full rounded-lg object-contain bg-ink-900/40"
            />
          );
        }
        return (
          <span
            key={`${att.resource_id ?? att.alt_text ?? 'att'}-${i}`}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-ink-800/60 text-[11px] text-ink-300"
          >
            <FileText size={12} className="shrink-0" />
            <span className="truncate max-w-[160px]">
              {att.alt_text ?? att.name ?? att.kind}
            </span>
          </span>
        );
      })}
    </div>
  );
}

export function MessageBubble({
  role,
  content,
  agentName,
  tokens,
  onApply,
  onCopy,
  timestamp,
  attachments,
  toolCalls,
  awaitingApproval,
  runId,
}: MessageBubbleProps): React.ReactElement {
  const { t } = useTranslation();
  const [copied, setCopied] = React.useState(false);

  // Denials-only read of useRunToolActivity: the header comment on that hook
  // bans the panel from consuming `activities` (it already has the trace via
  // `toolCalls` — reading both would double-render every tool_call). But
  // `denials` is a different event stream ("capability_denied") that never
  // appears in `toolCalls` at all, so reading it here duplicates nothing.
  const { denials } = useRunToolActivity(runId, false);

  // Split the turn's trace by renderer. The two arrays are disjoint and
  // together cover every call, so nothing is duplicated and nothing is lost.
  const { screenwritingActivities, otherCalls, writeSummary } = React.useMemo(() => {
    const calls = toolCalls ?? [];
    const screenwriting = calls.filter((c) => isScreenwritingTool(c.name));
    const activities = fromChatToolCalls(screenwriting);
    return {
      screenwritingActivities: activities,
      otherCalls: calls.filter((c) => !isScreenwritingTool(c.name)),
      writeSummary: summarizeWrites(activities),
    };
  }, [toolCalls]);

  const handleCopy = useCallback(() => {
    const plain = content.replace(/<[^>]+>/g, '');
    navigator.clipboard.writeText(plain).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
    onCopy?.();
  }, [content, onCopy]);

  if (role === 'user') {
    return (
      <div className="flex justify-end mb-3">
        <div className="max-w-[75%] px-3 py-2 rounded-xl bg-indigo-600/20 text-ink-200 text-sm leading-relaxed">
          {attachments && attachments.length > 0 && (
            <AttachmentStrip attachments={attachments} />
          )}
          <p className="whitespace-pre-wrap break-words">{content}</p>
          {timestamp && (
            <p className="mt-1 text-[10px] text-ink-500 text-right">{timestamp}</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start mb-3">
      <div className="max-w-[85%] rounded-xl bg-ink-800 text-ink-200 text-sm leading-relaxed overflow-hidden">
        {agentName && (
          <div className="px-3 pt-2 pb-1">
            <span className="text-[10px] text-amber-400 bg-amber-400/10 px-1.5 py-0.5 rounded font-medium">
              {agentName}
            </span>
          </div>
        )}

        {/* The trace is PARTITIONED, never rendered twice: screenwriting tools
            become chips + a write summary, everything else (Delegate / Skill /
            future tools) keeps the existing sub-task cards. */}
        <SubTaskList calls={otherCalls} />

        {denials.length > 0 && (
          <div className="px-3 pt-2">
            <CapabilityDeniedNotice denials={denials} />
          </div>
        )}

        {screenwritingActivities.length > 0 && (
          <div className="space-y-1.5 px-3 pt-2">
            <ToolActivityChips activities={screenwritingActivities} />
            <TurnWriteSummary summary={writeSummary} runId={runId} />
          </div>
        )}

        {awaitingApproval && <ApprovalCard approval={awaitingApproval} />}

        <div
          className="px-3 py-2 prose prose-invert prose-sm max-w-none"
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{
            __html: DOMPurify.sanitize(content, {
              ALLOWED_TAGS: ['h1', 'h2', 'h3', 'h4', 'p', 'strong', 'em', 'code', 'pre',
                             'hr', 'br', 'ul', 'ol', 'li', 'blockquote', 'a', 'span'],
              ALLOWED_ATTR: ['href', 'target', 'rel', 'class'],
              // Only permit http(s) and mailto: URLs. Blocks javascript:, data:, vbscript:,
              // etc. in href/src attributes to prevent XSS from LLM-generated links.
              ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
            }),
          }}
        />

        <div className="flex items-center gap-3 px-3 pb-2 pt-1 border-t border-ink-700/50">
          {tokens !== undefined && (
            <span className="text-[10px] text-ink-600">{tokens} tokens</span>
          )}
          <div className="flex items-center gap-2 ml-auto">
            {onApply && (
              <button
                type="button"
                onClick={onApply}
                className="text-[10px] text-[var(--accent-text)] hover:text-[var(--accent-text)] transition-colors"
              >
                Apply
              </button>
            )}
            <button
              type="button"
              onClick={handleCopy}
              className="flex items-center gap-0.5 text-[10px] text-ink-500 hover:text-ink-400 transition-colors"
            >
              {copied ? (
                <Check size={10} className="text-green-400" />
              ) : (
                <Copy size={10} />
              )}
              {copied ? t('chat.copied') : t('chat.copy')}
            </button>
          </div>
        </div>

        {timestamp && (
          <p className="px-3 pb-1 text-[10px] text-ink-600">{timestamp}</p>
        )}
      </div>
    </div>
  );
}
