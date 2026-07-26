/**
 * PromptSection — bilingual positive/negative AI-generation prompt block.
 *
 * Extracted from ResourceDetailPage (spec 2026-07-26-asset-prompt-management).
 * Sits AFTER the Tags picker, BEFORE Properties. Three states:
 *   - prompt data present  → collapsed one-line preview, click to expand
 *   - no data, trigger tag → collapsed "+ Add Prompt" row
 *   - no data, no tag      → light "+ Add Prompt" text entry
 * Expanding auto-applies the default trigger tag (onEnsureTriggerTag).
 */
import { useEffect, useState } from 'react';
import { ChevronRight, ChevronUp, Copy, Languages, Loader2, Send, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { Resource } from '../../types';
import { hasPromptData } from '../../utils/promptTriggerTags';
import { SendToCanvasModal } from './SendToCanvasModal';

export interface PromptSectionProps {
  resource: Resource;
  /** Optimistic local-merge + PATCH (page's handleResourceUpdate). */
  onPatch: (fields: Partial<Resource>) => void;
  /** True when any assigned tag has prompt_trigger. */
  hasTriggerTag: boolean;
  /** Apply the default trigger tag to this asset (page implements). */
  onEnsureTriggerTag: () => Promise<void>;
  /** Image assets get the Generate (reverse-engineer) button. */
  canGenerate: boolean;
  generating: boolean;
  onGenerate: () => void;
  translating: boolean;
  onTranslate: (lang: 'en' | 'zh') => void;
}

export function PromptSection({
  resource, onPatch, hasTriggerTag, onEnsureTriggerTag,
  canGenerate, generating, onGenerate, translating, onTranslate,
}: PromptSectionProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [lang, setLang] = useState<'en' | 'zh'>(() =>
    (resource.gen_prompt?.trim() ? 'en' : resource.gen_prompt_zh?.trim() ? 'zh' : 'en'),
  );
  const [posValue, setPosValue] = useState('');
  const [negValue, setNegValue] = useState('');
  const [sendToCanvasOpen, setSendToCanvasOpen] = useState(false);

  const posField = lang === 'zh' ? 'gen_prompt_zh' : 'gen_prompt';
  const negField = lang === 'zh' ? 'gen_prompt_negative_zh' : 'gen_prompt_negative';

  // Sync editors from the resource whenever id/lang/data changes.
  useEffect(() => {
    setPosValue((resource[posField] as string | null) || '');
    setNegValue((resource[negField] as string | null) || '');
  }, [resource.id, resource[posField], resource[negField], lang]);

  const dataPresent = hasPromptData(resource);
  const preview =
    (resource.gen_prompt || resource.gen_prompt_zh || '').split('\n')[0];

  const expand = () => {
    setExpanded(true);
    // Fire-and-forget: tag failure must not block editing.
    onEnsureTriggerTag().catch((err) => console.error('ensureTriggerTag:', err));
  };

  const commit = (field: string, value: string, current: string) => {
    if (value.trim() !== current.trim()) onPatch({ [field]: value.trim() } as Partial<Resource>);
  };

  const copyText = (text: string) => {
    if (text) navigator.clipboard.writeText(text).catch((e) => console.error(e));
  };

  if (!expanded && !dataPresent && !hasTriggerTag) {
    return (
      <div className="px-4 mt-2">
        <button onClick={expand} className="text-[11px] text-ink-600 hover:text-[var(--accent-text)] transition-colors">
          + {t('resources.infoPanel.addPrompt', 'Add Prompt')}
        </button>
      </div>
    );
  }

  if (!expanded) {
    return (
      <div className="px-4 mt-3">
        <button
          onClick={expand}
          className="w-full flex items-center gap-2 px-2.5 py-2 border border-ink-700/50 bg-ink-800/40 rounded-lg hover:border-[var(--accent-border)] transition-colors text-left"
        >
          <Sparkles size={12} className="text-ink-500 shrink-0" />
          <span className="text-[10px] text-ink-500 uppercase tracking-wider shrink-0">
            {t('resources.infoPanel.prompt', 'Prompt')}
          </span>
          <span className="flex-1 min-w-0 truncate font-mono text-[10.5px] text-ink-500">
            {dataPresent ? preview : `+ ${t('resources.infoPanel.addPrompt', 'Add Prompt')}`}
          </span>
          <ChevronRight size={12} className="text-ink-500 shrink-0" />
        </button>
      </div>
    );
  }

  const otherSidePos = lang === 'zh' ? resource.gen_prompt : resource.gen_prompt_zh;
  const otherSideNeg = lang === 'zh' ? resource.gen_prompt_negative : resource.gen_prompt_negative_zh;

  return (
    <div className="px-4 mt-3">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-ink-500 uppercase tracking-wider">
            {t('resources.infoPanel.prompt', 'Prompt')}
          </span>
          <div className="flex rounded overflow-hidden border border-ink-700/60">
            {(['en', 'zh'] as const).map((l) => (
              <button
                key={l}
                onClick={() => setLang(l)}
                className={`px-1.5 py-0.5 text-[9px] transition-colors ${
                  lang === l ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]' : 'text-ink-500 hover:text-ink-300'
                }`}
              >
                {l === 'en' ? 'EN' : '中'}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2.5">
          {canGenerate && (
            <button onClick={onGenerate} disabled={generating}
              title={t('resources.infoPanel.generatePromptHint', 'Reverse-engineer the prompt from this image')}
              className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors disabled:opacity-50">
              {generating ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}{' '}
              {t('resources.infoPanel.generatePrompt', 'Generate')}
            </button>
          )}
          {Boolean(otherSidePos || otherSideNeg) && (
            <button onClick={() => onTranslate(lang)} disabled={translating}
              title={t('resources.infoPanel.translatePromptHint', 'Translate from the other language')}
              className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors disabled:opacity-50">
              {translating ? <Loader2 size={11} className="animate-spin" /> : <Languages size={11} />}{' '}
              {t('resources.infoPanel.translatePrompt', 'Translate')}
            </button>
          )}
          {Boolean(posValue) && (
            <button onClick={() => copyText(posValue)}
              className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors">
              <Copy size={11} /> {t('resources.infoPanel.copyPrompt', 'Copy')}
            </button>
          )}
          <button onClick={() => setExpanded(false)}
            className="text-ink-500 hover:text-ink-300 transition-colors">
            <ChevronUp size={12} />
          </button>
        </div>
      </div>
      <textarea
        value={posValue}
        onChange={(e) => setPosValue(e.target.value)}
        onBlur={() => commit(posField, posValue, (resource[posField] as string | null) || '')}
        placeholder={t('resources.infoPanel.promptPlaceholder', 'Paste the AI generation prompt...')}
        rows={4}
        maxLength={20000}
        className="w-full bg-ink-800/50 border border-ink-700/50 rounded-lg px-2.5 py-2 text-xs font-mono text-ink-300 placeholder-ink-600 focus:outline-none focus:border-indigo-500/50 resize-none"
      />
      <div className="flex items-center justify-between mt-1.5 mb-1">
        <span className="text-[10px] text-red-400/85 uppercase tracking-wider">
          {t('resources.infoPanel.negativePrompt', 'Negative')}
        </span>
        {Boolean(negValue) && (
          <button onClick={() => copyText(negValue)}
            className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors">
            <Copy size={11} /> {t('resources.infoPanel.copyPrompt', 'Copy')}
          </button>
        )}
      </div>
      <textarea
        value={negValue}
        onChange={(e) => setNegValue(e.target.value)}
        onBlur={() => commit(negField, negValue, (resource[negField] as string | null) || '')}
        placeholder={t('resources.infoPanel.negativePromptPlaceholder', 'Negative prompt (what to avoid)...')}
        rows={2}
        maxLength={20000}
        className="w-full bg-red-500/[.06] border border-red-400/25 rounded-lg px-2.5 py-2 text-xs font-mono text-ink-300 placeholder-ink-600 focus:outline-none focus:border-red-400/50 resize-none"
      />
      {dataPresent && (
        <div className="flex justify-end mt-2">
          <button
            onClick={() => setSendToCanvasOpen(true)}
            className="flex items-center gap-1.5 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors"
          >
            <Send size={11} /> {t('resources.infoPanel.sendToCanvas', 'Send to Canvas')}
          </button>
        </div>
      )}
      {sendToCanvasOpen && (
        <SendToCanvasModal
          resource={resource}
          positive={posValue}
          negative={negValue.trim() ? negValue : null}
          onClose={() => setSendToCanvasOpen(false)}
        />
      )}
    </div>
  );
}
