import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Tag } from '../../types';
import { pickDefaultTarget } from './mergeTagsLogic';

interface MergeTagsDialogProps {
  tags: Tag[]; // the selected user tags (>=2)
  onConfirm: (targetId: string, sourceIds: string[]) => Promise<void>;
  onClose: () => void;
}

export const MergeTagsDialog: React.FC<MergeTagsDialogProps> = ({ tags, onConfirm, onClose }) => {
  const { t, i18n } = useTranslation();
  const [targetId, setTargetId] = useState<string>(
    () => pickDefaultTarget(tags) ?? String(tags[0].id),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const label = (tag: Tag) =>
    i18n.language === 'zh' && tag.name_zh ? tag.name_zh : tag.name;
  const sourceCount = useMemo(
    () =>
      tags
        .filter((x) => String(x.id) !== targetId)
        .reduce((s, x) => s + (x.media_count ?? 0), 0),
    [tags, targetId],
  );
  const target = tags.find((x) => String(x.id) === targetId) ?? tags[0];

  const handleMerge = async () => {
    setBusy(true);
    setError(null);
    try {
      const sources = tags
        .filter((x) => String(x.id) !== targetId)
        .map((x) => String(x.id));
      await onConfirm(targetId, sources);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Merge failed');
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="w-[360px] max-w-[90vw] bg-ink-900 border border-ink-700 rounded-xl p-4 space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-ink-100">
          {t('settings.tags.mergeTitle', 'Merge Tags')}
        </h3>
        <p className="text-xs text-ink-400">
          {t('settings.tags.mergePickTarget', 'Pick the tag to keep:')}
        </p>
        <div className="space-y-1 max-h-48 overflow-y-auto">
          {tags.map((tag) => (
            <label
              key={tag.id}
              className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-ink-800 cursor-pointer"
            >
              <input
                type="radio"
                name="merge-target"
                checked={String(tag.id) === targetId}
                onChange={() => setTargetId(String(tag.id))}
              />
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: tag.color || '#6366f1' }}
              />
              <span className="text-xs text-ink-200 flex-1 truncate">{label(tag)}</span>
              <span className="text-[10px] text-ink-500">({tag.media_count ?? 0})</span>
            </label>
          ))}
        </div>
        <p className="text-[11px] text-ink-500">
          {t('settings.tags.mergePreview', {
            defaultValue:
              'Up to {{count}} resources move to «{{name}}». {{n}} tags will be deleted.',
            count: sourceCount,
            name: label(target),
            n: tags.length - 1,
          })}
        </p>
        {error && <p className="text-[11px] text-red-400">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={onClose}
            disabled={busy}
            className="px-3 py-1.5 text-xs rounded bg-ink-800 text-ink-300 hover:bg-ink-700"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            onClick={handleMerge}
            disabled={busy}
            className="px-3 py-1.5 text-xs rounded bg-red-600 text-white hover:bg-red-500 disabled:opacity-50"
          >
            {busy ? t('settings.tags.merging', 'Merging…') : t('settings.tags.merge', 'Merge')}
          </button>
        </div>
      </div>
    </div>
  );
};
