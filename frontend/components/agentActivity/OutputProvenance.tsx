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
 * ⚠️ **No link is invented.** A URL assembled from `issue_id` would 404 or
 * land on an unrelated issue — the issue route is keyed by the issue KEY and
 * needs a team, neither of which can be derived from the snowflake. So the
 * Open Issue link is only ever one somebody else finished: the `deep_link`
 * the lineage itself carries (3a Task 3b), or an `issueHref` the host passes
 * to override it. Both absent — a run with no issue, an issue with no key or
 * no team — and the control stays disabled saying why.
 */
import React, { useEffect, useState, useSyncExternalStore } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { ExternalLink, GitCompare, PlayCircle, Sparkles } from 'lucide-react';

import {
  getOutputLineage,
  lineageGeneration,
  OutputsError,
  subscribeLineageChange,
  type OutputLineage,
} from '../../services/outputsService';
import { formatOutputCost, outputCostTitle } from './outputCost';
import { OutputDiffDialog } from '../Todolist/OutputDiffDialog';
import { useChildRun } from '../Todolist/childRunContext';
import type { DeliverableKind } from '../chat/deliverableKinds';

export interface OutputProvenanceProps {
  /** Which registry to ask. A union, not prose: every mount site names the
   *  kind as a LITERAL (`kind="script_shot"` on the shot node, `script_scene`
   *  in the scene block), so a typo is caught here rather than answered with a
   *  404 `not_registered` — the one failure this block renders as silence. */
  kind: DeliverableKind;
  /** The object's id as a STRING (Snowflake). Empty means "nothing to ask
   *  about yet" — panels render before their row arrives. */
  refId: string;
  /**
   * Where "Open Issue" goes — an OVERRIDE, not the only source.
   *
   * Left out, the block follows the `deep_link` the latest version carries,
   * which is the normal case on the object pages this ships on: they know
   * nothing about issues, and the lineage does. Pass one when the host knows
   * better — mounted on an issue page, it already knows which issue the
   * reader arrived from, and that beats the producing run's own issue.
   */
  issueHref?: string;
  /** 宿主已经读到的链 —— 传了就不自取。资源面板走的是另一个端点
   *  （`GET /resources/{id}/provenance`，键是资源 id 不是 generated_media id），
   *  同一块 UI 不该为此长出第二条取数分支。`undefined` = 自取；
   *  `null` = 宿主查过、没有来源（人手上传），渲染 null。
   *
   *  代价写明白：喂进来的链不参与本块的缓存失效（generation 按
   *  `kind`/`ref_id` 记账，而这条链的键是资源 id），所以它的新鲜度由宿主负责。 */
  lineage?: OutputLineage | null;
  /** 允许 Diff 按钮。媒体在资源面板上没有可比的版本文本（3b §6）。 */
  allowDiff?: boolean;
  className?: string;
}

const BTN =
  'inline-flex items-center gap-1 rounded border border-ink-700 px-2 py-1 text-[11px] text-ink-300 hover:border-info-line hover:text-info disabled:cursor-not-allowed disabled:opacity-50';

export const OutputProvenance: React.FC<OutputProvenanceProps> = ({
  kind,
  refId,
  issueHref,
  lineage: lineageProp,
  allowDiff,
  className,
}) => {
  const { t } = useTranslation();
  const childRun = useChildRun();
  const [lineage, setLineage] = useState<OutputLineage | null>(null);
  /** `null` = nothing wrong (including "not registered", which clears state
   *  entirely). A string is a code we have to SAY something about. */
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [diffOpen, setDiffOpen] = useState(false);
  // This block mounts where there is no WebSocket and no polling (a canvas
  // node, a library panel), so the cache's generation is the ONLY thing that
  // can tell it the chain moved. The service has already dropped the entry by
  // the time this fires, so re-reading reaches the transport.
  const gen = useSyncExternalStore(subscribeLineageChange, lineageGeneration, () => 0);

  // Pointing at a DIFFERENT object resets what is on screen. A refresh of the
  // SAME object must not: the generation is global, so an unrelated agent run
  // finishing would otherwise blank this block and shut an open version dialog
  // under the reader's hands.
  useEffect(() => {
    setLineage(null);
    setErrorCode(null);
    setDiffOpen(false);
  }, [kind, refId]);

  useEffect(() => {
    // 宿主给了答案就不再自问。`null` 也是答案（人手上传），所以判的是
    // `undefined` 而不是真值 —— 真值判定会把「查过、没有」退回成「自己去取」，
    // 对着一个本块根本不该问的端点。
    if (lineageProp !== undefined) {
      setLineage(lineageProp);
      setErrorCode(null);
      return;
    }
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
  }, [kind, refId, gen, lineageProp]);

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
  // The host's answer wins; the lineage's own link is the fallback, and both
  // may be absent. Neither is ever synthesised — see the note at the top.
  const issueUrl = issueHref ?? latest.deep_link;
  // 坐标还在、链接没了 = 被可见性抹掉，而不是「这个 run 本来就没有议题」。
  // 两种情形读者要采取的行动完全不同（去要权限 vs 没什么可去）。
  const redacted = latest.issue_id !== null && !issueUrl;
  const unlinkedTitle = redacted
    ? t('outputs.provenanceRedacted', 'Issue not visible to you')
    : t('outputs.provenanceNoLink', 'The issue that produced this is not linked');

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
          {/* Same pair as the thread card and the right rail — see outputCost.ts. */}
          <span
            data-testid="output-provenance-cost"
            title={outputCostTitle(latest.cost_cents, latest.cost_kind, { deliverableKind: lineage.kind, model: latest.model })}
            className="ml-2 tabular-nums"
          >
            {formatOutputCost(latest.cost_cents, latest.cost_kind)}
          </span>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {/* A LINK, not a button: middle-click and copy-address are what a
              reader does with "open the issue that made this". Rendered only
              when somebody handed one over — see the note at the top.

              `Link`, not a bare `<a>`: this block mounts INSIDE the canvas
              editor (ShotNodeView) and the script sheet, so a document reload
              would throw away the graph the reader is standing in. It still
              emits a real `href`, so middle-click and copy-address behave the
              way the paragraph above promises. */}
          {issueUrl ? (
            <Link data-testid="output-provenance-issue" to={issueUrl} className={BTN}>
              <ExternalLink size={11} />
              {t('outputs.provenanceOpenIssue', 'Open Issue')}
            </Link>
          ) : (
            <span
              data-testid="output-provenance-issue-unlinked"
              title={unlinkedTitle}
              className={`${BTN} cursor-not-allowed opacity-50`}
            >
              <ExternalLink size={11} />
              {t('outputs.provenanceOpenIssue', 'Open Issue')}
            </span>
          )}

          {/* Same contract Task 5's dialog uses: the run panel belongs to the
              issue page, so off that page there is nothing to open and the
              control says why rather than silently doing nothing.

              Redacted goes the same way, for a different reason: the run
              belongs to an issue this reader may not see, so opening its panel
              is not ours to offer. The coordinates stay on screen — the reader
              can name the run when asking for access — but the control says
              why instead of doing nothing when clicked (3b §5 稿四). */}
          <button
            type="button"
            data-testid="output-provenance-run"
            disabled={!childRun || redacted}
            title={
              redacted
                ? unlinkedTitle
                : childRun
                  ? undefined
                  : t('outputs.openRunHint', 'The run panel is not open here')
            }
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
          {allowDiff !== false && versions >= 2 && (
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
