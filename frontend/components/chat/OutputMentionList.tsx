/**
 * The Outputs tab's body: this issue's registered outputs, one row per citable
 * version (harness 3a Task 6).
 *
 * Owns exactly two things — which row is highlighted, and the ↑↓/Enter handle
 * the host routes keys into. Everything else (the transport, the rows, the
 * error) arrives as props from `useMentionOutputsTab`, the same division
 * `AssetGridPicker` has with `useMentionAssetsTab`: the tab that two composers
 * could disagree about is the one that must not own its own data.
 *
 * The handle mirrors `AssetGridPickerHandle` deliberately. The host's key
 * router already speaks that shape, and a second, subtly different one would
 * make "which tab claimed this keystroke" a question with two answers.
 */
import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import { FileOutput } from 'lucide-react';

import type { OutputMentionRow } from './outputMentionRows';

export interface OutputMentionListHandle {
  /** Move the highlight, wrapping. Driven by the host's arrow keys. */
  move: (delta: number) => void;
  /** Pick the highlighted row. False when there is nothing to pick — the host
   *  then lets Enter reach the text instead of swallowing it. */
  commitActive: () => boolean;
}

export interface OutputMentionListProps {
  /** Draw and claim keys only while the tab is showing. */
  active: boolean;
  rows: OutputMentionRow[];
  loading: boolean;
  /** One readable line. Null when the read succeeded — an empty shelf and a
   *  failed read are different answers. */
  error: string | null;
  onPick: (row: OutputMentionRow) => void;
}

/** The four kinds in the words a person uses — shared wording with the
 *  Outputs block's `KIND_LABEL`, kept as its own map because this list needs
 *  the singular noun alone (the block pairs it with a count). */
const KIND_LABEL: Record<string, [string, string]> = {
  generated_media: ['outputs.kindMedia', 'Image'],
  script_shot: ['outputs.kindShot', 'Shot'],
  script_scene: ['outputs.kindScene', 'Scene'],
  script_chapter: ['outputs.kindChapter', 'Chapter'],
};

export const OutputMentionList = forwardRef<OutputMentionListHandle, OutputMentionListProps>(
  function OutputMentionList({ active, rows, loading, error, onPick }, ref) {
    const { t } = useTranslation();
    const [index, setIndex] = useState(0);
    const scrollerRef = useRef<HTMLDivElement | null>(null);

    // The list is re-filtered on every keystroke of the `@` query, so the
    // highlight has to come home — otherwise typing one more character leaves
    // it pointing past the end and Enter picks nothing with no explanation.
    useEffect(() => {
      setIndex(0);
    }, [rows.length]);

    const move = useCallback(
      (delta: number) => {
        setIndex((cur) => {
          if (rows.length === 0) return 0;
          return (cur + delta + rows.length) % rows.length;
        });
      },
      [rows.length],
    );

    const commitActive = useCallback((): boolean => {
      const row = rows[index];
      if (!row) return false;
      onPick(row);
      return true;
    }, [rows, index, onPick]);

    useImperativeHandle(
      ref,
      () => ({
        move: (delta: number) => {
          if (active) move(delta);
        },
        commitActive: () => (active ? commitActive() : false),
      }),
      [active, move, commitActive],
    );

    if (!active) return null;

    if (error) {
      return (
        <div data-testid="output-picker-error" className="px-3 py-6 text-center text-[12px] text-danger">
          {error}
        </div>
      );
    }
    if (loading && rows.length === 0) {
      return (
        <div data-testid="output-picker-loading" className="px-3 py-6 text-center text-[12px] text-ink-500">
          {t('outputs.mentionLoading', 'Loading…')}
        </div>
      );
    }
    if (rows.length === 0) {
      return (
        <div data-testid="output-picker-empty" className="px-3 py-6 text-center text-[12px] text-ink-500">
          {t('outputs.mentionEmpty', 'This issue has produced nothing yet')}
        </div>
      );
    }

    return (
      <div
        ref={scrollerRef}
        data-testid="output-picker-list"
        className="py-1 max-h-[288px] overflow-y-auto"
      >
        {rows.map((row, idx) => {
          const [key, fallback] = KIND_LABEL[row.ref_kind] ?? ['outputs.kindOther', row.ref_kind.replace(/_/g, ' ')];
          const kindWord = t(key, fallback);
          return (
            <React.Fragment key={row.key}>
              {/* Drawn once, immediately above the fold — the rows below are
                  still citable, they are just not what "the thing as it stands
                  now" means. */}
              {row.startsOlderGroup && (
                <div
                  data-testid="output-picker-older"
                  className="px-2.5 pt-2 pb-1 text-[10px] uppercase tracking-wide text-ink-600"
                >
                  {t('outputs.mentionOlder', 'Older')}
                </div>
              )}
              <button
                type="button"
                data-testid="output-picker-row"
                data-ref={row.ref_id}
                data-kind={row.ref_kind}
                data-version={row.version}
                data-latest={row.latest ? 'true' : 'false'}
                onClick={() => onPick(row)}
                onMouseEnter={() => setIndex(idx)}
                className={`w-full text-left flex items-center gap-2 px-2.5 py-1.5 rounded ${
                  idx === index ? 'bg-[var(--accent-soft)]' : 'hover:bg-ink-800/50'
                }`}
              >
                <FileOutput size={12} className="shrink-0 text-info" />
                <span className="flex-1 min-w-0">
                  <span className="block text-[12px] text-ink-100 truncate">
                    {/* An untitled row prints its kind and id rather than a
                        blank line — the registry allows a null title. */}
                    {row.title ?? `${kindWord} #${row.ref_id}`}
                  </span>
                  <span className="block text-[10px] text-ink-500">{kindWord}</span>
                </span>
                <span className="shrink-0 rounded border border-info-line px-1 text-[11px] text-info tabular-nums">
                  {t('outputs.version', 'v{{n}}', { n: row.version })}
                </span>
              </button>
            </React.Fragment>
          );
        })}
      </div>
    );
  },
);
