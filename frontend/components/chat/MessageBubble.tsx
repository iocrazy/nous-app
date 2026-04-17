import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import DOMPurify from 'dompurify';
import { Copy, Check } from 'lucide-react';

export interface MessageBubbleProps {
  role: 'user' | 'assistant';
  content: string;
  agentName?: string;
  tokens?: number;
  onApply?: () => void;
  onCopy?: () => void;
  timestamp?: string;
}

export function MessageBubble({
  role,
  content,
  agentName,
  tokens,
  onApply,
  onCopy,
  timestamp,
}: MessageBubbleProps): React.ReactElement {
  const { t } = useTranslation();
  const [copied, setCopied] = React.useState(false);

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
        <div className="max-w-[75%] px-3 py-2 rounded-xl bg-indigo-600/20 text-zinc-200 text-sm leading-relaxed">
          <p className="whitespace-pre-wrap break-words">{content}</p>
          {timestamp && (
            <p className="mt-1 text-[10px] text-zinc-500 text-right">{timestamp}</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start mb-3">
      <div className="max-w-[85%] rounded-xl bg-zinc-800 text-zinc-200 text-sm leading-relaxed overflow-hidden">
        {agentName && (
          <div className="px-3 pt-2 pb-1">
            <span className="text-[10px] text-amber-400 bg-amber-400/10 px-1.5 py-0.5 rounded font-medium">
              {agentName}
            </span>
          </div>
        )}

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

        <div className="flex items-center gap-3 px-3 pb-2 pt-1 border-t border-zinc-700/50">
          {tokens !== undefined && (
            <span className="text-[10px] text-zinc-600">{tokens} tokens</span>
          )}
          <div className="flex items-center gap-2 ml-auto">
            {onApply && (
              <button
                type="button"
                onClick={onApply}
                className="text-[10px] text-indigo-400 hover:text-indigo-300 transition-colors"
              >
                Apply
              </button>
            )}
            <button
              type="button"
              onClick={handleCopy}
              className="flex items-center gap-0.5 text-[10px] text-zinc-500 hover:text-zinc-400 transition-colors"
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
          <p className="px-3 pb-1 text-[10px] text-zinc-600">{timestamp}</p>
        )}
      </div>
    </div>
  );
}
