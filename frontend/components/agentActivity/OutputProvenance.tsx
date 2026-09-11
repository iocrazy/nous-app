/**
 * Where this object came from — mounted on an object's own page (3a Task 6).
 *
 * The block answers one question a reader has while looking at a shot, a scene
 * or a resource: did an agent make this, and if so, in which run? Until now
 * the registry held that answer and nothing on the object's page asked it.
 *
 * ⚠️ **A human-made object renders NOTHING.** `GET /outputs/{kind}/{ref_id}`
 * answers 404 `not_registered` when no run ever registered the object, and
 * that is the normal state of most rows in the library — it is a fact about
 * the object, not a failure to read it. Drawing an error there would put a
 * permanent false alarm on nearly every page, and readers would learn to
 * ignore the block before it ever had something true to say. Every OTHER
 * failure does render, for the mirror-image reason: silence would be
 * indistinguishable from "a person made this".
 *
 * ⚠️ **No link is invented.** The lineage response carries `issue_id` and
 * `run_id` and no key or deep link, while the issue route is keyed by the
 * issue KEY and needs a team — so a URL assembled from the snowflake would
 * 404, or land on an unrelated issue. The Open Issue link therefore appears
 * only when a host hands one over (`issueHref`). No host does today; see the
 * note on the prop.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, GitCompare, PlayCircle, Sparkles } from 'lucide-react';

import {
  getOutputLineage,
  OutputsError,
  type OutputLineage,
} from '../../services/outputsService';
import { OutputDiffDialog } from '../Todolist/OutputDiffDialog';
import { useChildRun } from '../Todolist/childRunContext';

export interface OutputProvenanceProps {
  /** One of `generated_media | script_shot | script_scene | script_chapter`. */
  kind: string;
  /** The object's id as a STRING (Snowflake). Empty means "nothing to ask
   *  about yet" — panels render before their row arrives. */
  refId: string;
  /**
   * Where "Open Issue" goes, when the host can work it out.
   *
   * Optional and usually absent: the lineage endpoint returns no issue key and
   * no team, and this component refuses to build a URL from `issue_id` alone.
   * A host that already knows the issue (a future issue-side mount) can pass
   * one; the object pages this ships on cannot, so they get the fact without
   * the link rather than a link that goes nowhere.
   */
  issueHref?: string;
  className?: string;
}

const BTN =
  'inline-flex items-center gap-1 rounded border border-ink-700 px-2 py-1 text-[11px] text-ink-300 hover:border-info-line hover:text-info disabled:cursor-not-allowed disabled:opacity-50';

export const OutputProvenance: React.FC<OutputProvenanceProps> = ({
  kind,
  refId,
  issueHref,
  className,
}) => {
  const { t } = useTranslation();
  const childRun = useChildRun();
  const [lineage, setLineage] = useState<OutputLineage | null>(null);
  /** `null` = nothing wrong (including "not registered", which clears state
   *  entirely). A string is a code we have to SAY something about. */
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [diffOpen, setDiffOpen] = useState(false);

  useEffect(() => {
    setLineage(null);
    setErrorCode(null);
    setDiffOpen(false);
    if (!refId) return;
    let live = true;
    getOutputLineage(kind, refId)
      .then((chain) => {
        if (live) setLineage(chain);
      })
      .catch((err) => {
        if (!live) return;
        // `not_registered` is an ANSWER: a person made this object. Logged at
        // debug level only — it is the common case, and console.error on every
        // human-made object would drown the log it is meant to serve.
        if (err instanceof OutputsError && err.code === 'not_registered') return;
        console.error('[OutputProvenance] lineage read failed', err);
        setErrorCode(err instanceof OutputsError ? err.code : '');
      });
    return () => {
      live = false;
    };
  }, [kind, refId]);

  if (errorCode !== null) {
    return (
      <p
        data-testid="output-provenance-error"
        data-code={errorCode || 'unknown'}
        className={`text-[11px] text-danger ${className ?? ''}`}
      >
        {t('outputs.provenanceFailed', 'Could not read where this came from.')}
      </p>
    );
  }

  // Nothing registered, or the read has not answered yet. Both draw nothing:
  // a skeleton would flash on every human-made object before vanishing.
  if (!lineage || lineage.versions.length === 0) return null;

  const latest = lineage.versions[0];
  const versions = lineage.versions.length;

  return (
    <>
      {/* `data-step` duplicates what the sentence below says in words. The
          attribute is what a test can read without depending on an
          initialised i18n instance, and what a debugger reads at a glance. */}
      <div
        data-testid="output-provenance"
        data-kind={lineage.kind}
        data-ref={lineage.ref_id}
        data-run={latest.run_id}
        data-versions={versions}
        data-step={latest.step ?? ''}
        className={`rounded border border-ink-800 bg-ink-900/40 p-2 ${className ?? ''}`}
      >
        <div className="flex items-center gap-1.5 text-[11px] text-ink-400">
          <Sparkles size={11} className="shrink-0 text-agent" />
          <span>{t('outputs.provenance', 'Made By An Agent')}</span>
          <span className="ml-auto tabular-nums text-ink-500">
            {t('outputs.provenanceVersions', {
              count: versions,
              defaultValue: `${versions} versions`,
            })}
          </span>
        </div>

        <div className="mt-1 text-[11px] text-ink-500">
          {latest.step !== null && (
            <span className="tabular-nums">
              {t('outputs.provenanceStep', 'Step {{n}}', { n: latest.step })}
            </span>
          )}
          {latest.created_at && (
            <span className="ml-2 tabular-nums">{latest.created_at.slice(0, 16).replace('T', ' ')}</span>
          )}
          {latest.model && <span className="ml-2 truncate">{latest.model}</span>}
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {/* A LINK, not a button: middle-click and copy-address are what a
              reader does with "open the issue that made this". Rendered only
              when the host resolved one — see the prop's note. */}
          {issueHref ? (
            <a data-testid="output-provenance-issue" href={issueHref} className={BTN}>
              <ExternalLink size={11} />
              {t('outputs.provenanceOpenIssue', 'Open Issue')}
            </a>
          ) : (
            <span
              data-testid="output-provenance-issue-unlinked"
              title={t('outputs.provenanceNoLink', 'The issue that produced this is not linked')}
              className={`${BTN} cursor-not-allowed opacity-50`}
            >
              <ExternalLink size={11} />
              {t('outputs.provenanceOpenIssue', 'Open Issue')}
            </span>
          )}

          {/* Same contract Task 5's dialog uses: the run panel belongs to the
              issue page, so off that page there is nothing to open and the
              control says why rather than silently doing nothing. */}
          <button
            type="button"
            data-testid="output-provenance-run"
            disabled={!childRun}
            title={childRun ? undefined : t('outputs.openRunHint', 'The run panel is not open here')}
            onClick={() =>
              childRun?.open({
                childRunId: latest.run_id,
                parentRunId: null,
                step: latest.step ?? 0,
                mode: 'sync',
                subagentType: lineage.kind.replace(/_/g, ' '),
                description: latest.title ?? `${lineage.kind} #${lineage.ref_id}`,
              })
            }
            className={BTN}
          >
            <PlayCircle size={11} />
            {t('outputs.provenanceOpenRun', 'Open Run')}
          </button>

          {/* Only with something to compare against. A Diff control on a
              single-version object could never work, and a control that can
              never work reads as broken rather than as absent. */}
          {versions >= 2 && (
            <button
              type="button"
              data-testid="output-provenance-diff"
              onClick={() => setDiffOpen(true)}
              className={BTN}
            >
              <GitCompare size={11} />
              {t('outputs.provenanceDiff', 'Diff')}
            </button>
          )}
        </div>
      </div>

      {diffOpen && (
        <OutputDiffDialog
          kind={lineage.kind}
          refId={lineage.ref_id}
          title={latest.title}
          initialTo={lineage.latest_version}
          onClose={() => setDiffOpen(false)}
        />
      )}
    </>
  );
};
