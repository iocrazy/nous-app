// features/canvas-core/library/useLibraryDrop.ts
//
// The one place a DROP says what it did.
//
// `dropLibraryItems` produces a fully typed outcome and the three landings each
// fill it in — but a `void dropLibraryItems(...)` at every call site threw all
// of it away, so dragging an asset whose image will not resolve onto a prompt
// node showed a highlight appear, a highlight disappear, and nothing else. The
// panel's CLICK path reports the very same outcomes through `useOptionalToast`,
// which left the two ways to do one thing disagreeing about whether failure is
// visible. That is the silent no-op this repo keeps re-learning
// (CLAUDE.md: 用户动作→agent 触发的每条路径必须返回类型化结果).
//
// It lives in ONE hook rather than at the three call sites because all three
// of those files (CanvasSurface, PromptNodeView, MediaNodeView) sit at a line
// budget, and three copies of the same message ladder is exactly how two of
// them would drift.
//
// The COPY is the click path's copy, reused key for key. A drop and a click
// that produce the same outcome must not describe it in two different ways.

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';

import { useOptionalToast } from '../../../components/Toast';
import {
  dropLibraryItems,
  type LibraryDropOutcome,
  type LibraryDropTarget,
} from './dropLibraryItems';
import type { LibraryItem } from './librarySearch';
import { mentionLibraryItems, type MentionInserters } from './mentionLibraryItems';

/** Which toast a message asks for. `error` is reserved for a real refusal —
 *  "already there" and "over quota" are outcomes, not faults. */
export type DropMessage = { text: string; tone: 'error' | 'info' };

/**
 * What to SAY about an outcome, or `null` when the drop simply worked and the
 * board already shows it.
 *
 * Pure so the ladder can be pinned without a DOM. The order matters and is the
 * order a user reads it in: a failure outranks everything; then a drop that
 * changed nothing because it was all already there (indistinguishable from
 * success without a word); then a quota refusal that took some but not all.
 */
export function describeDropOutcome(
  outcome: LibraryDropOutcome,
  target: LibraryDropTarget,
  t: TFunction,
): DropMessage | null {
  if (outcome.failed > 0) {
    // The empty pane is a PLACEMENT, not a reference: an audio upload dropped
    // there could never have become a reference, so saying it "could not be
    // added as a reference" describes an attempt that was never made. The
    // click path has said `placeFailed` there all along — this is the same
    // split the `alreadyOnCanvas` branch below already makes.
    return target.kind === 'canvas'
      ? {
          text: t('canvas.library.placeFailed', {
            count: outcome.failed,
            defaultValue: '{{count}} could not be placed',
          }),
          tone: 'error',
        }
      : {
          text: t('canvas.library.someFailed', {
            count: outcome.failed,
            defaultValue: '{{count}} could not be added as references',
          }),
          tone: 'error',
        };
  }
  if (outcome.placed + outcome.referenced === 0 && outcome.skipped > 0) {
    // Nothing moved and nothing is marked, so silence here looks exactly like
    // success. The canvas landing says "on this canvas"; the two node landings
    // say "on this node", which is the click path's own split.
    return target.kind === 'canvas'
      ? {
          text: t('canvas.library.alreadyOnCanvas', {
            count: outcome.skipped,
            defaultValue: '{{count}} already on this canvas',
          }),
          tone: 'info',
        }
      : {
          text: t('canvas.library.alreadyReferenced', {
            count: outcome.skipped,
            defaultValue: '{{count}} already on this node',
          }),
          tone: 'info',
        };
  }
  if (outcome.clamped > 0) {
    return {
      text: t('canvas.library.quotaClamped', {
        count: outcome.clamped,
        defaultValue: '{{count}} references not added · quota reached',
      }),
      tone: 'info',
    };
  }
  return null;
}

/**
 * Run a drop and report it.
 *
 * `opts.maxRefs` is the target model's ceiling, which only a NODE can know —
 * it comes from `useModelCapabilities`, and the surface's empty-pane landing
 * has no target to ask. Passed straight through to `dropLibraryItems`.
 */
export function useLibraryDrop(
  scopeId: string,
  opts?: { maxRefs?: number },
): (items: readonly LibraryItem[], target: LibraryDropTarget) => Promise<LibraryDropOutcome> {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const maxRefs = opts?.maxRefs;
  return useCallback(
    async (items, target) => {
      // Every KNOWN rejection is already typed one level down, so this catch
      // is the guard against a future one — and an unhandled rejection inside
      // a drop handler looks exactly like a drop that worked. Reported as
      // every item failing, which is what did happen.
      let outcome: LibraryDropOutcome;
      try {
        outcome = await dropLibraryItems(items, target, scopeId, { maxRefs });
      } catch (err) {
        console.error('[useLibraryDrop] drop threw:', err);
        outcome = { handled: true, placed: 0, referenced: 0, mentioned: 0,
          failed: items.length, skipped: 0, clamped: 0 };
      }
      const message = describeDropOutcome(outcome, target, t);
      if (message) toast?.addToast(message.text, message.tone);
      return outcome;
    },
    [scopeId, maxRefs, t, toast],
  );
}

/**
 * Run an `⌥` mention drop on ONE prompt node and report it.
 *
 * The sibling of `useLibraryDrop`, and here rather than in
 * `mentionLibraryItems.ts` for the reason that module is pure: the insert
 * needs an editor handle only the node holds, and the ECHO needs `t` and the
 * toast. Keeping the two runners in one file is what stops the drop path and
 * the mention path from growing two different vocabularies for one failure —
 * the message goes through `describeDropOutcome`, key for key.
 */
export function useLibraryMention(
  scopeId: string,
  nodeId: string,
): (items: readonly LibraryItem[], handle: MentionInserters | null) => Promise<void> {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  return useCallback(
    async (items, handle) => {
      if (!handle || items.length === 0) return;
      let failed: number;
      let mentioned = 0;
      try {
        const r = await mentionLibraryItems(items, scopeId, handle);
        mentioned = r.mentioned;
        failed = r.failed.length;
      } catch (err) {
        // Same guard as the drop path above, for the same reason.
        console.error('[useLibraryMention] mention threw:', err);
        failed = items.length;
      }
      const message = describeDropOutcome(
        { handled: true, placed: 0, referenced: 0, mentioned,
          failed, skipped: 0, clamped: 0 },
        { kind: 'prompt', nodeId, mention: true },
        t,
      );
      if (message) toast?.addToast(message.text, message.tone);
    },
    [scopeId, nodeId, t, toast],
  );
}
