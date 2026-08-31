// frontend/components/resources/assets/useAssetFailure.ts
//
// One place that turns a rejected asset-library request into something the
// user can read. Extracted from `AssetShelf` (Task 6) when the entity sheet
// (Task 7) needed the same behaviour for a dozen more mutations.
//
// The rule it encodes: a TYPED backend refusal renders its own sentence
// (`assets.err.<code>`), and anything else renders the generic one AFTER
// being logged. The failure is never swallowed — an asset-library action that
// quietly does nothing is the silent-no-op class this repo keeps re-learning
// (CLAUDE.md, "触发路径必须类型化失败回显").
//
// `logTag` names the caller in the console line, so a report of "it just
// didn't work" can be traced to the component that asked.

import { useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { useToast } from '../../Toast';
import { GeneratedApiError } from '../../../services/assetsService';

export type AssetFailureReporter = (err: unknown) => void;

/**
 * A reporter plus a ref holding the LATEST one.
 *
 * The ref exists because `reportFailure` closes over `t`, whose identity
 * react-i18next does not promise to keep stable: an effect that fetches and
 * depends on the reporter directly would re-run whenever i18next handed back
 * a new `t`, i.e. a refetch loop. Effects read `ref.current`; event handlers
 * can use `report` itself.
 */
export function useAssetFailureReporter(logTag: string): {
  report: AssetFailureReporter;
  reportRef: React.MutableRefObject<AssetFailureReporter>;
} {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const report = useCallback<AssetFailureReporter>(
    (err) => {
      if (err instanceof GeneratedApiError) {
        // Logged as well as shown: the toast is transient and carries no
        // request id, and a typed code the locale has no string for would
        // otherwise leave nothing behind to diagnose.
        console.error(`[${logTag}] request refused (${err.code}):`, err);
        addToast(t(`assets.err.${err.code}`, t('assets.err.generic')), 'error');
        return;
      }
      console.error(`[${logTag}] request failed:`, err);
      addToast(t('assets.err.generic'), 'error');
    },
    [addToast, t, logTag],
  );

  const reportRef = useRef(report);
  reportRef.current = report;

  return { report, reportRef };
}
