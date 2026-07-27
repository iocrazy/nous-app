/**
 * PromptTriggerTagsCard — Settings → Tags card managing which tags act as
 * "Prompt tags" (tags.prompt_trigger). Assets carrying any of these show
 * the Prompt panel + grid badge; the first one is auto-applied when a
 * prompt is added to an asset.
 */
import { useEffect, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { fetchAllTags, updateTag } from '../services/unifiedTagService';
import type { Tag } from '../types';

export function PromptTriggerTagsCard() {
  const { t } = useTranslation();
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    fetchAllTags().then(setAllTags).catch((e) => console.error('fetchAllTags:', e));
  }, []);

  const triggers = allTags.filter((tg) => tg.prompt_trigger);
  const candidates = allTags.filter((tg) => !tg.prompt_trigger && tg.type === 'user');

  const setFlag = async (tag: Tag, value: boolean) => {
    setBusy(String(tag.id));
    try {
      const updated = await updateTag(String(tag.id), { prompt_trigger: value });
      setAllTags((prev) => prev.map((tg) => (String(tg.id) === String(tag.id) ? { ...tg, ...updated } : tg)));
    } catch (err) {
      console.error('updateTag prompt_trigger:', err);
    } finally {
      setBusy(null);
      setAdding(false);
    }
  };

  return (
    <section className="bg-ink-900 border border-ink-800 rounded-xl p-5 mb-4">
      <h3 className="text-sm font-semibold text-ink-200">
        {t('settings.promptTriggerTags.title', 'Prompt Trigger Tags')}
      </h3>
      <p className="text-xs text-ink-500 mt-1 mb-4">
        {t('settings.promptTriggerTags.hint',
          'Assets with any of these tags show the Prompt panel and card badge. The first tag is applied automatically when a prompt is added.')}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {triggers.map((tag) => (
          <span key={String(tag.id)}
            className="inline-flex items-center gap-1.5 text-xs pl-3 pr-1.5 py-1 rounded-full border border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]">
            {tag.name}
            <button
              aria-label={`Remove ${tag.name}`}
              disabled={busy === String(tag.id)}
              onClick={() => setFlag(tag, false)}
              className="w-4 h-4 rounded-full inline-flex items-center justify-center hover:bg-ink-700/60 disabled:opacity-50"
            >
              <X size={10} />
            </button>
          </span>
        ))}
        {adding ? (
          <select
            autoFocus
            onBlur={() => setAdding(false)}
            onChange={(e) => {
              const tag = candidates.find((tg) => String(tg.id) === e.target.value);
              if (tag) setFlag(tag, true);
            }}
            className="bg-ink-800 border border-ink-700 rounded-full text-xs text-ink-300 px-3 py-1 focus:outline-none"
            defaultValue=""
          >
            <option value="" disabled>{t('settings.promptTriggerTags.pick', 'Pick a tag…')}</option>
            {candidates.map((tg) => (
              <option key={String(tg.id)} value={String(tg.id)}>{tg.name}</option>
            ))}
          </select>
        ) : (
          <button onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1 text-xs px-3 py-1 rounded-full border border-dashed border-ink-600 text-ink-500 hover:text-[var(--accent-text)] hover:border-[var(--accent-border)] transition-colors">
            <Plus size={11} /> {t('settings.promptTriggerTags.add', 'Add tag')}
          </button>
        )}
      </div>
    </section>
  );
}
