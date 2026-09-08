import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import { Copy, Check, FileText, Shapes } from 'lucide-react';

import type { AIChatMessageAttachment, ChatToolCall } from '../../types';
import { getResourceCoverUrl } from '../../services/resourceService';
import { ResourceThumb } from './ResourceThumb';
import { ApprovalCard, type AwaitingApproval } from './ApprovalCard';
import { QuestionCard } from '../Todolist/QuestionCard';
import type { TypedQuestion } from '../Todolist/questionTypes';
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
   * Attachments sent with this turn: images render inline, library
   * references as a cover+name chip, anything else as a filename chip.
   * Persisted server-side so history reloads keep them; the optimistic
   * bubble adds a local preview data URL for the images it just uploaded.
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
  /**
   * Phase 2a: the turn parked on a typed question (AskUser). Renders the
   * QuestionCard under the prose; answered/superseded stamps come with it.
   */
  awaitingInput?: TypedQuestion;
  /** Answer the parked question — sends a message with `answer_to`. */
  onAnswerQuestion?: (value: string, answerTo: string) => Promise<void>;
  /** Read-only card (an older assistant message: only the newest is open). */
  awaitingInputDisabled?: boolean;
  /** The agent_runs id that backed this turn (metadata_json.run_id). Threaded
   *  to TurnWriteSummary so its Undo button knows what to undo. */
  runId?: string | null;
}

/** Agents emit **markdown**, not HTML. This bubble used to run the text
    through DOMPurify and inject it as innerHTML, so every `**bold**` and
    `> quote` reached the user as literal syntax characters.

    Security note: react-markdown does not parse embedded raw HTML (no
    rehype-raw here, on purpose), so an LLM-authored `<img onerror=...>`
    renders as literal text rather than a live element, and its
    defaultUrlTransform drops `javascript:`/`data:` hrefs. That makes the
    sanitizer unnecessary rather than merely redundant. The cost of the
    tradeoff is that intentional inline HTML shows up as text — acceptable
    for chat prose.

    Styling is per-element (same idiom as AILibrary/MarkdownBody.tsx),
    tightened for bubble density. The `prose prose-invert prose-sm` classes
    this replaced were inert anyway — @tailwindcss/typography is not
    installed, so they styled nothing. */
const MARKDOWN_PLUGINS = [remarkGfm, remarkBreaks];

const MARKDOWN_COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="mt-3 mb-1.5 text-base font-bold text-ink-100 first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-3 mb-1.5 text-[15px] font-semibold text-ink-100 first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-2.5 mb-1 text-sm font-semibold text-ink-100 first:mt-0">{children}</h3>
  ),
  p: ({ children }) => (
    <p className="my-1.5 break-words first:mt-0 last:mb-0">{children}</p>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-ink-100">{children}</strong>
  ),
  ul: ({ children }) => (
    <ul className="my-1.5 ml-5 list-disc space-y-0.5 marker:text-ink-500 first:mt-0 last:mb-0">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="my-1.5 ml-5 list-decimal space-y-0.5 marker:text-ink-500 first:mt-0 last:mb-0">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="my-0.5">{children}</li>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-[var(--accent-text)] underline-offset-2 hover:underline"
    >
      {children}
    </a>
  ),
  code: ({ className: cls, children, ...rest }) => {
    const inline = !/language-/.test(cls ?? '');
    if (inline) {
      return (
        <code
          className="rounded bg-ink-900/70 px-1 py-0.5 font-mono text-[12px] text-warn"
          {...rest}
        >
          {children}
        </code>
      );
    }
    return (
      <code className={cls} {...rest}>
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-lg border border-ink-700 bg-ink-900 p-2.5 font-mono text-[12px] leading-relaxed text-ink-200">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-ink-600 pl-3 italic text-ink-400">
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-[12px]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-ink-700 px-2 py-1 text-left font-semibold text-ink-100">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-ink-800 px-2 py-1">{children}</td>
  ),
  hr: () => <hr className="my-3 border-ink-700" />,
};

/**
 * The icon family for a library reference.
 *
 * The persisted attachment keeps only `kind: 'resource_ref'` plus `mime`
 * (the reducer's key whitelist in `conversations_ai_store.py` drops
 * everything else), so the asset's own kind — the thing the picker chip
 * reads straight off the row — has to be derived here instead.
 */
function refKindFromMime(mime: string | null | undefined): string {
  if (!mime) return 'doc';
  if (mime.startsWith('video/')) return 'video';
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime === 'application/pdf') return 'pdf';
  return 'doc';
}

/**
 * Cover URL for a reference, or null when there is no id worth asking about.
 *
 * `'None'` is not defensive padding: a script path wrote Python's
 * `str(None)` into the column, and those rows are still in history. Asking
 * for `/resources/None/cover` would 404 on every render of that bubble.
 */
function refCoverUrl(resourceId: string | number | null | undefined): string | null {
  const id = resourceId === null || resourceId === undefined ? '' : String(resourceId);
  if (!id || id === 'None' || id === 'null' || id === 'undefined') return null;
  return getResourceCoverUrl(id);
}

/**
 * A library asset the user attached, as it appears in their own bubble.
 *
 * Same family as the composer's staged chip (agent tokens, cover + name),
 * one notch tighter and with no × — history is not editable. The cover has
 * to come from the id because the wire shape carries no `thumbnail_url`:
 * the composer's snapshot fields die at the boundary, so a reloaded bubble
 * knows the asset only by id and mime.
 */
function ResourceRefChip({
  attachment,
}: {
  attachment: AIChatMessageAttachment;
}): React.ReactElement {
  const { t } = useTranslation();
  // Both keys are live: this panel sends `alt_text`, the issue reply box
  // sends `name`, and both land in the same column.
  const label =
    attachment.alt_text ?? attachment.name ?? t('chat.attachments.resourceRef');
  return (
    <span
      data-testid="bubble-resource-chip"
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-agent-line bg-agent-soft text-agent text-[11px] max-w-full"
    >
      <ResourceThumb
        thumbnailUrl={refCoverUrl(attachment.resource_id)}
        kind={refKindFromMime(attachment.mime)}
        iconSize={11}
        imgClassName="w-4 h-4 rounded object-cover shrink-0"
        iconClassName="inline-flex shrink-0"
        imgTestId="bubble-resource-chip-thumb"
        iconTestId="bubble-resource-chip-icon"
      />
      <span className="truncate max-w-[160px]">{label}</span>
    </span>
  );
}

/**
 * A library ASSET the user attached, as it appears in their own bubble.
 *
 * Same family as the resource chip beside it (agent tokens, name, no × —
 * history is not editable), and the same reason for its shape: the persisted
 * attachment keeps only the whitelisted keys, so a reloaded bubble knows the
 * asset by `asset_id` / `name` and nothing else.
 *
 * NO COVER AND NO TYPE ICON, both on purpose and for one reason: neither
 * `cover_file_id` nor `asset_type` crosses the boundary. They are
 * composer-side snapshots; the wire shape omits them and the reducer's key
 * whitelist would drop them anyway. Guessing a cover from `asset_id` would
 * 404 on every render (an asset id is not a resource id), and painting a type
 * icon from the optimistic snapshot alone would make the chip CHANGE the
 * moment history reloaded — the exact popping this strip was fixed to stop.
 * One neutral library glyph renders identically before and after the reload.
 */
function AssetRefChip({
  attachment,
}: {
  attachment: AIChatMessageAttachment;
}): React.ReactElement {
  const { t } = useTranslation();
  // `name` first: that is the key the asset attachment sends and the reducer
  // persists. `alt_text` is read as a fallback only because both keys land in
  // the same column and a future sender may use the other one.
  const label =
    attachment.name ?? attachment.alt_text ?? t('chat.attachments.assetRef');
  return (
    <span
      data-testid="bubble-asset-chip"
      data-asset-id={attachment.asset_id ?? ''}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-agent-line bg-agent-soft text-agent text-[11px] max-w-full"
    >
      <Shapes size={11} className="shrink-0" data-testid="bubble-asset-chip-icon" />
      <span className="truncate max-w-[160px]">{label}</span>
    </span>
  );
}

/** Images, file chips, and library references shown above the message text.
    References used to be dropped here on the grounds that the @-mention was
    already in the text; it no longer is — the picker moved chips out of the
    sentence into a staging row, and the send path strips the `@query`
    trigger — so dropping them left a bubble that said nothing about which
    asset the turn was actually about, or an empty bubble when the asset was
    the whole message. */
function AttachmentStrip({
  attachments,
}: {
  attachments: AIChatMessageAttachment[];
}): React.ReactElement | null {
  if (attachments.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 mb-1.5">
      {attachments.map((att, i) => {
        const key = `${att.resource_id ?? att.asset_id ?? att.alt_text ?? 'att'}-${i}`;
        if (att.kind === 'resource_ref') {
          return <ResourceRefChip key={key} attachment={att} />;
        }
        // P5. Without this branch an asset falls through to the grey filename
        // chip below and reads as an unrecognised file — it does not crash,
        // which is exactly why it would have gone unnoticed.
        if (att.kind === 'asset_ref') {
          return <AssetRefChip key={key} attachment={att} />;
        }
        const src =
          att.preview_data_url ??
          (att.kind === 'image' && att.resource_id
            ? getResourceCoverUrl(String(att.resource_id))
            : undefined);
        if (src) {
          return (
            <img
              key={key}
              src={src}
              alt={att.alt_text ?? 'attachment'}
              loading="lazy"
              className="max-h-40 max-w-full rounded-lg object-contain bg-ink-900/40"
            />
          );
        }
        return (
          <span
            key={key}
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
  awaitingInput,
  onAnswerQuestion,
  awaitingInputDisabled = false,
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

  // Copies the raw markdown. It used to strip `<...>` on the assumption the
  // content was HTML; it never was, and markdown source is what a user wants
  // to paste elsewhere anyway.
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(content).catch(() => {});
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
          {/* An asset-only turn has no text at all. Rendering the empty
              paragraph anyway is what produced the blank bubble — the chips
              above ARE the message in that case. */}
          {content.trim().length > 0 && (
            <p className="whitespace-pre-wrap break-words">{content}</p>
          )}
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

        <div className="px-3 py-2 max-w-none break-words" data-testid="bubble-markdown">
          <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS} components={MARKDOWN_COMPONENTS}>
            {content}
          </ReactMarkdown>
        </div>

        {awaitingInput && (
          <div className="px-3 pb-2" data-testid="bubble-question">
            <QuestionCard
              question={awaitingInput}
              disabled={awaitingInputDisabled}
              onAnswer={onAnswerQuestion ?? (async () => undefined)}
            />
          </div>
        )}

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
