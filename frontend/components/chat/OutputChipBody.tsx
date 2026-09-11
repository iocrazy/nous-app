/**
 * A staged CITATION, as a chip (harness 3a Task 6).
 *
 * Reads `@<title> v<n>`. The version is not decoration: a citation is
 * version-locked, so a chip that named only the object would be the same chip
 * whichever revision the reader picked — and revising the object afterwards
 * would silently re-point what the comment meant. The number is the only thing
 * on screen that says which one.
 *
 * Deliberately thinner than `AssetChipBody`: no thumbnail, no menu. A citation
 * delivers coordinates, not a thing, and there is nothing to preview that the
 * reader has not already seen in the picker.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { FileOutput, X } from 'lucide-react';

export interface OutputChipBodyProps {
  refKind: string;
  refId: string;
  version: number;
  /** Registry title snapshot; null rows fall back to kind + id. */
  title: string | null;
  /**
   * Un-stage this draft citation. OMITTED in the thread (3a Task 8b): a
   * posted comment is a record, so an X on it would either do nothing or
   * edit history. Without the handler the button is not rendered at all —
   * a disabled X still reads as "removable, just not now".
   */
  onRemove?: () => void;
}

const KIND_LABEL: Record<string, [string, string]> = {
  generated_media: ['outputs.kindMedia', 'Image'],
  script_shot: ['outputs.kindShot', 'Shot'],
  script_scene: ['outputs.kindScene', 'Scene'],
  script_chapter: ['outputs.kindChapter', 'Chapter'],
};

export const OutputChipBody: React.FC<OutputChipBodyProps> = ({
  refKind,
  refId,
  version,
  title,
  onRemove,
}) => {
  const { t } = useTranslation();
  const [key, fallback] = KIND_LABEL[refKind] ?? ['outputs.kindOther', refKind.replace(/_/g, ' ')];
  const name = title ?? `${t(key, fallback)} #${refId}`;
  return (
    <span
      data-testid="staged-output-chip"
      data-ref={refId}
      data-kind={refKind}
      data-version={version}
      className="group inline-flex items-center gap-1.5 px-2 py-0.5 bg-ink-800 border border-ink-700 rounded text-[11px] text-ink-300"
      title={`${name} · ${t('outputs.version', 'v{{n}}', { n: version })}`}
    >
      <FileOutput className="w-3 h-3 shrink-0 text-info" />
      <span className="truncate max-w-[140px]">{`@${name}`}</span>
      <span className="shrink-0 tabular-nums text-info">
        {t('outputs.version', 'v{{n}}', { n: version })}
      </span>
      {onRemove && (
        <button
          type="button"
          data-testid="staged-output-chip-remove"
          onClick={onRemove}
          aria-label={t('outputs.citationRemove', 'Remove This Reference')}
          title={t('outputs.citationRemove', 'Remove This Reference')}
          className="text-ink-500 hover:text-danger transition-colors"
        >
          <X className="w-3 h-3" />
        </button>
      )}
    </span>
  );
};
