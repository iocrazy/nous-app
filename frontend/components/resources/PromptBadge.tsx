/**
 * PromptBadge — grid-card corner chip for assets carrying an AI prompt.
 * Hovering the chip (not the whole card) opens a preview popover with
 * truncated positive/negative text and a copy action. Renders null when
 * the asset has no prompt data.
 */
import { useState } from 'react';
import { Copy, Sparkles } from 'lucide-react';

import type { Resource } from '../../types';
import { hasPromptData } from '../../utils/promptTriggerTags';

export function PromptBadge({ resource, shiftLeft = false }: { resource: Resource; shiftLeft?: boolean }) {
  const [open, setOpen] = useState(false);
  if (!hasPromptData(resource)) return null;

  const positive = resource.gen_prompt || resource.gen_prompt_zh || '';
  const negative = resource.gen_prompt_negative || resource.gen_prompt_negative_zh || '';

  return (
    <div
      className={`absolute bottom-1.5 ${shiftLeft ? 'left-1.5' : 'right-1.5'} z-10`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onClick={(e) => e.stopPropagation()}
    >
      {open && (
        <div className="absolute bottom-full mb-1.5 right-0 w-56 bg-ink-950 border border-ink-700 rounded-lg shadow-xl p-2.5 z-20 cursor-default">
          <div className="flex items-center justify-between">
            <span className="text-[9px] text-ink-500 uppercase tracking-widest">Prompt</span>
            <button
              onClick={() => navigator.clipboard.writeText(positive).catch((e) => console.error(e))}
              className="flex items-center gap-1 text-[9px] text-[var(--accent-text)] hover:opacity-80"
            >
              <Copy size={10} /> Copy
            </button>
          </div>
          <p className="mt-0.5 font-mono text-[10px] leading-relaxed text-ink-300 line-clamp-3 break-all">{positive}</p>
          {negative && (
            <>
              <span className="mt-1.5 block text-[9px] text-red-400/85 uppercase tracking-widest">Negative</span>
              <p className="mt-0.5 font-mono text-[10px] leading-relaxed text-red-300/80 line-clamp-2 break-all">{negative}</p>
            </>
          )}
        </div>
      )}
      <span className="inline-flex items-center gap-1 bg-ink-950/75 backdrop-blur-sm border border-[var(--accent-border)] text-[var(--accent-text)] text-[9px] font-semibold rounded-full px-2 py-0.5 cursor-default">
        <Sparkles size={10} /> Prompt
      </span>
    </div>
  );
}
