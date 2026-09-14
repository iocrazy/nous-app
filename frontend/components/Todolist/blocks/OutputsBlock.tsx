/**
 * Context block: what this issue's agents actually produced (harness 3a §5).
 *
 * The unit is the OBJECT, not the registration row — three revisions of one
 * shot are one line that reads v3, because "the agent made three things" and
 * "the agent revised one thing twice" are different facts. Clicking a line
 * opens its version dialog.
 *
 * An issue that produced nothing draws no card at all (every issue matches, so
 * a permanent empty card would sit on every page). A read that FAILED still
 * draws one, saying so: "produced nothing" and "I could not find out" are
 * answers a person acts on differently.
 *
 * ⚠️ Block id is `outputs`, not `deliverables` — that id belongs to the
 * project-stage folder block and `registerIssueBlock` throws on a duplicate.
 */
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FileOutput } from 'lucide-react';

import { listIssueOutputs, type OutputObject } from '../../../services/outputsService';
import { formatOutputCost, outputCostTitle } from '../../agentActivity/outputCost';
import type { DeliverableKind } from '../../chat/deliverableKinds';
import type { IssueBlock, IssueBlockProps } from '../issueBlocks';
import { useTurnSignal, type TurnSignal } from '../issueTurnSignal';
import { OutputDiffDialog } from '../OutputDiffDialog';
import { setHighlightedOutput } from '../outputHighlight';
import { RailCard } from './StatusBlock';

/** How long «Updated» stays up after a re-read. Long enough to catch the eye of
 *  someone already looking at the card, short enough that it never becomes a
 *  label the reader stops seeing. */
export const UPDATED_BADGE_MS = 1_000;

/** The four kinds, in the words a person uses for them.
 *
 *  Keyed by `DeliverableKind` so a kind added to `deliverableKinds.ts` fails
 *  to compile until it is labelled here too. Exported for
 *  `components/chat/deliverableKinds.test.ts`, which re-checks the coverage at
 *  runtime — the type alone can be widened back in one keystroke. */
export const KIND_LABEL: Record<DeliverableKind, [string, string]> = {
  generated_media: ['outputs.kindMedia', 'Image'],
  script_shot: ['outputs.kindShot', 'Shot'],
  script_scene: ['outputs.kindScene', 'Scene'],
  script_chapter: ['outputs.kindChapter', 'Chapter'],
};

export const OutputsBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const { t } = useTranslation();
  const issueId = Number(ctx.issue.id);
  const refreshKey = ctx.env.refreshKey ?? 0;
  const [items, setItems] = useState<OutputObject[]>([]);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<OutputObject | null>(null);
  const [justUpdated, setJustUpdated] = useState(false);
  // «A turn on this issue ended» — one broadcast, deduped by watermark, so the
  // WS `done` frame and the polling edge for one turn cost one re-read.
  const signal = useTurnSignal(String(issueId));
  /** The signal the badge has already spoken for. The effect re-runs for other
   *  reasons too (a rail `refreshKey` bump), and `signal !== null` stays true
   *  forever once the first turn lands — so without this the badge would claim
   *  an update on every later re-read. A badge that cries wolf is worse than
   *  no badge. */
  const badgedSignal = useRef<TurnSignal | null>(null);

  // A ring must not outlive the rail that set it (navigating away mid-hover).
  useEffect(() => () => setHighlightedOutput(null), []);

  useEffect(() => {
    let live = true;
    // Claimed at the START of the run this signal caused, so a re-run for any
    // other reason cannot claim it a second time.
    const isNewSignal = signal !== null && signal !== badgedSignal.current;
    badgedSignal.current = signal;
    listIssueOutputs(issueId)
      .then((rows) => {
        if (!live) return;
        setItems(rows);
        setFailed(false);
        // Only for the signal's own re-read: the first load of a page is not
        // "this changed", and a badge on arrival would say the opposite.
        if (isNewSignal) setJustUpdated(true);
      })
      .catch((err) => {
        if (!live) return;
        console.error('[OutputsBlock] load failed', err);
        setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [issueId, refreshKey, signal]);

  useEffect(() => {
    if (!justUpdated) return;
    const timer = window.setTimeout(() => setJustUpdated(false), UPDATED_BADGE_MS);
    return () => window.clearTimeout(timer);
  }, [justUpdated]);

  if (items.length === 0 && !failed) return null;

  return (
    <>
      <RailCard title={t('issueDetail.outputs', 'Outputs')} testId="outputs-block">
        {justUpdated && (
          <span
            data-testid="outputs-updated"
            className="animate-in fade-in mb-1 block text-[11px] text-info"
          >
            {t('outputs.updated', 'Updated')}
          </span>
        )}
        {items.map((item) => {
          // The cast asserts nothing about the value — it only permits the
          // lookup; `??` still answers for a kind this build has not heard of.
          const [key, fallback] = KIND_LABEL[item.kind as DeliverableKind] ?? ['outputs.kindOther', item.kind.replace(/_/g, ' ')];
          const revisions = Math.max(0, item.latest_version - 1);
          return (
            <button
              key={`${item.kind}:${item.ref_id}`}
              type="button"
              data-testid="outputs-row"
              data-kind={item.kind}
              data-ref={item.ref_id}
              onClick={() => setOpen(item)}
              // Pointing at a row rings the matching card in the thread, which
              // is how a person sees WHICH step produced this object. The key
              // names the latest version — the card the thread ends on.
              onMouseEnter={() => setHighlightedOutput(`${item.kind}:${item.ref_id}:${item.latest_version}`)}
              onMouseLeave={() => setHighlightedOutput(null)}
              onFocus={() => setHighlightedOutput(`${item.kind}:${item.ref_id}:${item.latest_version}`)}
              onBlur={() => setHighlightedOutput(null)}
              className="flex w-full items-start gap-2 py-1 text-left text-[12px] hover:bg-ink-900/60"
            >
              <FileOutput size={11} className="mt-0.5 shrink-0 text-info" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-ink-300">{item.title ?? `${t(key, fallback)} #${item.ref_id}`}</span>
                <span className="block text-[11px] text-ink-500">
                  {t(key, fallback)}
                  {revisions > 0 && ` · ${t('outputs.revisions', '{{count}} revisions', { count: revisions })}`}
                </span>
              </span>
              {/* The latest version's price — the row IS that version. */}
              <span data-testid="outputs-row-cost" className="shrink-0 text-[11px] text-ink-500 tabular-nums"
                    title={outputCostTitle(item.versions[0]?.cost_cents ?? null, item.versions[0]?.cost_kind ?? null, { deliverableKind: item.kind, model: item.versions[0]?.model ?? null })}>
                {formatOutputCost(item.versions[0]?.cost_cents ?? null, item.versions[0]?.cost_kind ?? null)}
              </span>
              <span className="shrink-0 rounded border border-info-line px-1 text-[11px] text-info tabular-nums">
                {t('outputs.version', 'v{{n}}', { n: item.latest_version })}
              </span>
            </button>
          );
        })}
        {failed && (
          <p data-testid="outputs-error" className="mt-1 break-words text-[11px] text-danger">
            {t('outputs.loadFailed', 'Could not read this issue’s outputs.')}
          </p>
        )}
      </RailCard>
      {open && (
        <OutputDiffDialog
          kind={open.kind}
          refId={open.ref_id}
          title={open.title}
          initialTo={open.latest_version}
          onClose={() => setOpen(null)}
        />
      )}
    </>
  );
};

export const outputsBlock: IssueBlock = {
  id: 'outputs',
  zone: 'context',
  // Above Subtasks / Budget (50): what the agent MADE outranks how much it
  // cost. Any issue can have outputs, so the matcher is open and the
  // component is what decides there is nothing to draw.
  order: 25,
  match: () => true,
  component: OutputsBlockView,
};
