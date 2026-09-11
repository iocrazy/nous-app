/**
 * The built-in node renderers. Semantic tokens only (ok / warn / danger /
 * info / agent), no emoji, English copy via i18n. Registered explicitly at
 * the bottom — importing this module is what installs them.
 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ChevronDown, ChevronRight, Clock, FileOutput, Inbox, MessageSquare, Quote, RotateCw, ShieldOff, Users, Wallet, Wrench, GitFork } from 'lucide-react';

import { schedulesService } from '../../../../services/schedulesService';
import { fmtWhen, fmtWhenCompact } from '../../../../utils/fmtWhen';
import { generatedMediaCoverUrl } from '../../../../services/generatedMediaService';
import { useChildRun } from '../../../Todolist/childRunContext';
import { useHighlightedOutput } from '../../../Todolist/outputHighlight';
import { OutputDiffDialog } from '../../../Todolist/OutputDiffDialog';
import type {
  BudgetNode,
  DeniedNode,
  ErrorNode,
  InboxNode,
  OutputCard,
  ScheduleNode,
  StepLine,
  StepNode,
  TurnEndNode,
  UserNode,
} from '../foldEvents';
import { registerTrajectoryNode, type NodeProps } from './registry';
import { useTrajectoryRunId } from '../trajectoryRunContext';

function fmtMs(ms: number | null): string {
  if (ms === null) return '';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function fmtCents(c: number | null): string {
  return c === null ? '' : `¢${c.toFixed(c < 1 ? 3 : 2)}`;
}

/** A child's spend, where zero is not a price.
 *
 *  A turn that reached a model always costs something once rates are known,
 *  so 0 here means the cost was never computed — a missing price row, or (up
 *  to Task 7b defect A) an envelope that dropped the field. `¢0.000` states
 *  the opposite: that the child was free. Same rule `RunMetaLine` writes down
 *  — "an unpriced run should read as 'no data', not 'free'". */
function fmtChildCents(c: number | null): string {
  return c === null || c === 0 ? '—' : fmtCents(c);
}

const Row: React.FC<React.PropsWithChildren<{ className?: string; testId?: string }>> = ({
  className = '',
  testId,
  children,
}) => (
  <div className={`flex items-center gap-2 px-2.5 py-1.5 text-xs ${className}`} data-testid={testId}>
    {children}
  </div>
);

export const UserNodeView: React.FC<NodeProps<UserNode>> = ({ node }) => {
  const { t } = useTranslation();
  return (
    <Row className="text-ink-300" testId="traj-user">
      <MessageSquare size={12} className="shrink-0 text-info" />
      <span className="truncate">
        <span className="text-ink-500">{t('trajectory.you')}: </span>
        {node.text}
      </span>
    </Row>
  );
};

function lineLabel(line: StepLine, t: (k: string, o?: Record<string, unknown>) => string): string {
  switch (line.type) {
    case 'tool':
      return line.label;
    case 'retry':
      return t('trajectory.retries', { count: line.count, attempt: line.label });
    case 'compaction':
      return t('trajectory.compaction');
    case 'todo':
      return line.label || t('trajectory.todoUpdated');
    case 'output':
      return t('trajectory.output', { chars: (line.detail?.chars as number) ?? line.label.length });
    default:
      return line.label;
  }
}

function summaryText(node: StepNode, t: (k: string, o?: Record<string, unknown>) => string): string {
  const parts: string[] = [];
  if (node.summary.tools) parts.push(t('trajectory.tools', { count: node.summary.tools }));
  if (node.summary.retries) parts.push(t('trajectory.retries', { count: node.summary.retries }));
  if (node.summary.compactions) parts.push(t('trajectory.compaction'));
  if (node.summary.todo) parts.push(`${node.summary.todo.done}/${node.summary.todo.total}`);
  if (node.summary.outputs) parts.push(t('trajectory.outputs', { count: node.summary.outputs }));
  return parts.join(' · ');
}

/** Jump to the forked run's bubble in the same timeline (IssueChatThread gives each run row `id="run-<id>"`). */
function scrollToRun(runId: string): void {
  if (typeof document === 'undefined') return;
  document.getElementById(`run-${runId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/** `60` → "60s", `60.004` → "60.0s" (decimals=1), `600` → "10 min", `600.02` → "10.0 min". */
function fmtSeconds(s: number | null | undefined, decimals = 0): string {
  if (typeof s !== 'number' || !Number.isFinite(s)) return '?';
  if (s >= 120) return `${decimals > 0 ? (s / 60).toFixed(1) : String(Math.round((s / 60) * 100) / 100)} min`;
  return `${decimals > 0 ? s.toFixed(decimals) : String(Math.round(s * 100) / 100)}s`;
}

/**
 * Sub-agents dispatched by one step (harness 2b-2 §5-1). Three states, and
 * the difference between them is the whole point: a foreground child means
 * "wait, the parent is blocked on this", a background one means "go do
 * something else, the answer lands in the inbox", a finished one is a
 * receipt. Only a child with a run id can be opened — a background task that
 * has not started has no run to show.
 */
type ChildState = 'running' | 'queued' | 'done' | 'failed';

const CHILD_TONE: Record<ChildState, string> = {
  running: 'border-agent-line bg-agent-soft/40 text-agent',
  queued: 'border-info-line bg-info-soft/40 text-info',
  done: 'border-ok-line bg-ok-soft/40 text-ink-300',
  failed: 'border-danger-line bg-danger-soft/40 text-danger',
};

/** The only statuses that mean the child actually delivered. Anything else a
 *  child can end as — `failed`, `cancelled`, a timeout — is a non-result, and
 *  drawing it in ok-green tells the reader the opposite of the truth. */
const CHILD_OK = new Set(['completed', 'ok', 'succeeded', 'success']);

/** True once the child has ended, whatever the verdict. */
export function childState(status: string | null, mode: 'sync' | 'async'): ChildState {
  if (!status) return mode === 'async' ? 'queued' : 'running';
  return CHILD_OK.has(status.toLowerCase()) ? 'done' : 'failed';
}

export const SubagentCards: React.FC<{ node: StepNode }> = ({ node }) => {
  const { t } = useTranslation();
  const childRun = useChildRun();
  // The run these events belong to — the child's panel header names it, and
  // this card is the only place that knows (Task 7b defect H).
  const parentRunId = useTrajectoryRunId();
  if (node.children.length === 0) return null;
  return (
    <div className="flex flex-col gap-1 px-2.5 pb-1.5 pl-7" data-testid="subagent-cards">
      {node.children.map((c) => {
        const state = childState(c.status, c.mode);
        const spend = [fmtMs(c.durationMs), fmtChildCents(c.costCents)].filter(Boolean).join(' · ');
        return (
          <div
            key={c.key}
            data-testid="subagent-card"
            data-state={state}
            data-mode={c.mode}
            className={`rounded-md border px-2 py-1 text-[11px] ${CHILD_TONE[state]}`}
          >
            <div className="flex min-w-0 items-center gap-1.5">
              {state === 'running' && (
                <span className="inline-block h-2.5 w-2.5 shrink-0 animate-spin rounded-full border-2 border-agent-line border-t-agent" />
              )}
              <Users size={11} className="shrink-0" />
              <span className="font-medium">{c.subagentType}</span>
              {c.continuedFrom && (
                <span data-testid="subagent-continued" className="shrink-0 rounded border border-agent-line px-1">
                  {t('subagent.continued', 'Continued From #{{run}}', { run: c.continuedFrom.slice(-6) })}
                </span>
              )}
              <span className="truncate text-ink-400">{c.description}</span>
              <span className="ml-auto shrink-0 tabular-nums">
                {state === 'running' && t('subagent.waiting', 'Waiting For Result')}
                {state === 'queued' && t('subagent.queued', 'Background · Result Arrives In The Inbox')}
                {state === 'done' && [t('subagent.done', '✓ Done'), spend].filter(Boolean).join(' · ')}
                {state === 'failed' && [t('subagent.failed', 'Failed'), spend].filter(Boolean).join(' · ')}
              </span>
              {c.childRunId && childRun && (
                <button
                  type="button"
                  data-testid="subagent-open"
                  className="shrink-0 underline decoration-dotted"
                  onClick={() =>
                    childRun.open({
                      childRunId: c.childRunId as string,
                      parentRunId,
                      step: node.step,
                      mode: c.mode,
                      subagentType: c.subagentType,
                      description: c.description,
                    })
                  }
                >
                  {t('subagent.open', 'Open Run #{{run}}', { run: c.childRunId.slice(-6) })}
                </button>
              )}
            </div>
            {c.summary && (
              <div className="mt-0.5 truncate pl-4 text-ink-400" data-testid="subagent-summary">
                {c.summary}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export const StepNodeView: React.FC<NodeProps<StepNode>> = ({ node, expanded, onToggle, marks }) => {
  const { t } = useTranslation();
  const open = node.live || expanded;
  const tail = [fmtMs(node.summary.durationMs), fmtCents(node.summary.costCents)].filter(Boolean).join(' · ');
  // `data-step` + `data-turn`: a node's identity is the PAIR (see foldEvents'
  // `step:${turn}:${step}` key), so a reader addressing one step from outside
  // — a lineage deep link — needs both halves in the DOM. The step alone is
  // ambiguous on every run that took more than one turn.
  return (
    <div
      className={`rounded-md ${node.live ? 'border border-ok-line bg-ok-soft/40' : ''}`}
      data-testid={node.live ? 'traj-step-live' : 'traj-step'}
      data-step={node.step}
      data-turn={node.turn}
    >
      <button
        type="button"
        onClick={() => onToggle?.(node)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs text-ink-400 hover:text-ink-200"
        aria-expanded={open}
      >
        {node.live ? (
          <span className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-ok-line border-t-ok" />
        ) : open ? (
          <ChevronDown size={11} className="shrink-0 text-ink-600" />
        ) : (
          <ChevronRight size={11} className="shrink-0 text-ink-600" />
        )}
        <span className={`font-mono text-[10px] tracking-widest ${node.live ? 'text-ok' : 'text-ink-600'}`}>
          {t('trajectory.step', { n: node.step }).toUpperCase()}
          {node.live ? ` · ${t('trajectory.live').toUpperCase()}` : ''}
        </span>
        {!open && <span className="truncate text-ink-400">{summaryText(node, t)}</span>}
        <span className="ml-auto shrink-0 tabular-nums text-[11px] text-ink-600">{tail}</span>
      </button>
      {marks && marks.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 px-2.5 pb-1" data-testid="trajectory-fork-marks">
          {marks.map((runId) => (
            <button
              key={runId}
              type="button"
              data-testid="trajectory-fork-mark"
              data-run={runId}
              onClick={(e) => {
                e.stopPropagation();
                scrollToRun(runId);
              }}
              className="inline-flex items-center gap-1 rounded border border-info-line bg-info-soft px-1.5 py-0.5 text-[11px] text-info hover:brightness-110"
            >
              <GitFork size={10} /> {t('fork.mark', 'Fork → #{{run}}', { run: runId.slice(-6) })}
            </button>
          ))}
        </div>
      )}
      {open && (
        <div className="flex flex-col gap-0.5 pb-1.5 pl-7 pr-2.5" data-testid="traj-step-lines">
          {node.lines.map((line) => (
            <div key={line.key} className="flex items-center gap-2 text-xs text-ink-400" data-testid={`traj-line-${line.type}`}>
              {line.type === 'retry' ? (
                <RotateCw size={11} className="shrink-0 text-warn" />
              ) : (
                <Wrench size={11} className={`shrink-0 ${line.ok ? 'text-agent' : 'text-danger'}`} />
              )}
              <span className="truncate">{lineLabel(line, t)}</span>
              {line.type === 'tool' && line.detail?.timedOut === true ? (
                <>
                  <span className="text-danger" data-testid="trajectory-timeout-badge">
                    {t('trajectory.timedOut', { s: fmtSeconds(line.detail?.timeoutS as number | null) })}
                  </span>
                  <span className="text-ink-600" data-testid="trajectory-timeout-elapsed">
                    {t('trajectory.elapsed', { s: fmtSeconds(line.detail?.elapsedS as number | null, 1) })}
                  </span>
                </>
              ) : (
                line.type === 'tool' && !line.ok && <span className="text-danger">{t('trajectory.failed')}</span>
              )}
              <span className="ml-auto shrink-0 tabular-nums text-[11px] text-ink-600">{fmtMs(line.durationMs)}</span>
            </div>
          ))}
          {node.live && node.lines.length === 0 && (
            <div className="text-xs text-ink-500">{t('trajectory.thinking')}</div>
          )}
        </div>
      )}
      {/* Outside the `open` block on purpose: a dispatched sub-agent and a
          registered output are the two things about a collapsed step you
          still need to see. */}
      <OutputCitations node={node} />
      <SubagentCards node={node} />
      <OutputCards node={node} />
    </div>
  );
};

/**
 * What this step registered (harness 3a §5). Two states, and the difference
 * is the whole point: a FIRST registration means the agent made something
 * that did not exist, a REVISION means it replaced something that did — and
 * the card has to say which version it replaced, or the reader cannot tell a
 * third draft from a third object.
 *
 * `Open` shows that one version on its own; `Diff` (revisions only) puts it
 * beside the version it replaced. Spend follows fmtChildCents: an unpriced
 * row reads `—`, never `¢0.000`.
 */
/**
 * A generated image's cover. `ref_id` IS the generated_media id, so the URL is
 * already owned by `generatedMediaCoverUrl` — the same helper the Generated
 * cards, the cleanup dialog and the generation-history panel use. Nothing is
 * built by hand here: a fifth copy of the string is a fifth place to be wrong
 * when the route moves. (`resolveMediaUrl` stays for the diff endpoint, whose
 * media URLs arrive relative off the wire.)
 */
function outputThumbUrl(card: OutputCard): string | null {
  if (card.kind !== 'generated_media') return null;
  return generatedMediaCoverUrl(card.refId);
}

/** 56×40 so a row of cards keeps the thread's rhythm; a cover that 404s or is
 *  not readable falls back to a neutral box rather than a broken-image glyph. */
const OutputThumb: React.FC<{ card: OutputCard }> = ({ card }) => {
  const [failed, setFailed] = React.useState(false);
  const url = outputThumbUrl(card);
  if (!url || failed) {
    return url ? (
      <span
        data-testid="output-thumb-missing"
        className="h-[40px] w-[56px] shrink-0 rounded border border-ink-800 bg-ink-900"
        aria-hidden="true"
      />
    ) : null;
  }
  return (
    <img
      data-testid="output-thumb"
      src={url}
      alt={card.title ?? `${card.kind} #${card.refId}`}
      loading="lazy"
      onError={() => setFailed(true)}
      className="h-[40px] w-[56px] shrink-0 rounded border border-ink-800 object-cover"
    />
  );
};

/**
 * What this turn POINTED AT on its way in (harness 3a T8c 缺陷 4).
 *
 * The sentence carries one fact the rest of the UI never states: a citation is
 * pinned to the version the person picked and does not follow later revisions.
 * Without it a reader seeing "@MEDIUM v2" beside an object now on v5 cannot
 * tell whether the agent read v2 or v5.
 *
 * Sits above the step's own cards on purpose — what went in, then what came
 * out — and outside the `open` block with them: a collapsed step still has to
 * say what it was pointed at.
 */
export const OutputCitations: React.FC<{ node: StepNode }> = ({ node }) => {
  const { t } = useTranslation();
  const cited = node.citations ?? [];
  if (cited.length === 0) return null;
  return (
    <div
      className="flex flex-wrap items-center gap-1.5 px-2.5 pb-1.5 pl-7 text-[11px] text-ink-500"
      data-testid="output-citations"
    >
      <Quote size={11} className="shrink-0" />
      <span className="shrink-0">
        {t('outputs.cited', 'References {{count}} outputs · pinned to version', { count: cited.length })}
      </span>
      {cited.map((c) => (
        <span
          key={c.key}
          data-testid="output-citation-chip"
          data-kind={c.kind}
          data-ref={c.refId}
          data-version={String(c.version)}
          className="min-w-0 truncate rounded border border-ink-800 bg-ink-900/60 px-1.5 py-0.5 text-ink-400"
        >
          {t('outputs.citedChip', '@{{title}} v{{n}}', {
            title: c.title ?? `${c.kind.replace(/_/g, ' ')} #${c.refId}`,
            n: c.version,
          })}
        </span>
      ))}
    </div>
  );
};

export const OutputCards: React.FC<{ node: StepNode }> = ({ node }) => {
  const { t } = useTranslation();
  // Which card's dialog is open, and whether it was opened as a comparison.
  const [open, setOpen] = React.useState<{ card: OutputCard; compare: boolean } | null>(null);
  // What the rail is pointing at (harness 3a §5): hovering a row there rings
  // the card here, so a person can tell which step made which output.
  const highlighted = useHighlightedOutput();
  if (node.outputs.length === 0) return null;
  return (
    <div className="flex flex-col gap-1 px-2.5 pb-1.5 pl-7" data-testid="output-cards">
      {node.outputs.map((o) => {
        const revised = o.version > 1;
        const parent = o.parentVersion ?? o.version - 1;
        return (
          <div
            key={o.key}
            data-testid="output-card"
            data-state={revised ? 'revised' : 'new'}
            data-kind={o.kind}
            data-highlighted={highlighted === o.key ? 'true' : 'false'}
            className={`rounded-md border px-2 py-1 text-[11px] ${
              revised ? 'border-warn-line bg-warn-soft/40 text-warn' : 'border-ok-line bg-ok-soft/40 text-ink-300'
            } ${highlighted === o.key ? 'ring-2 ring-info' : ''}`}
          >
            <div className="flex min-w-0 items-center gap-1.5">
              <OutputThumb card={o} />
              <FileOutput size={11} className="shrink-0" />
              <span className="shrink-0 font-medium tabular-nums">
                {revised
                  ? t('outputs.replaced', 'v{{n}} ← v{{prev}}', { n: o.version, prev: parent })
                  : t('outputs.version', 'v{{n}}', { n: o.version })}
              </span>
              <span className="truncate text-ink-400">{o.title ?? `${o.kind.replace(/_/g, ' ')} #${o.refId}`}</span>
              {o.model && <span className="shrink-0 truncate text-ink-500">{o.model}</span>}
              <span className="ml-auto shrink-0 tabular-nums text-ink-500">{fmtChildCents(o.costCents)}</span>
              <button
                type="button"
                data-testid="output-open"
                className="shrink-0 underline decoration-dotted"
                onClick={() => setOpen({ card: o, compare: false })}
              >
                {t('outputs.open', 'Open')}
              </button>
              {revised && (
                <button
                  type="button"
                  data-testid="output-diff-open"
                  className="shrink-0 underline decoration-dotted"
                  onClick={() => setOpen({ card: o, compare: true })}
                >
                  {t('outputs.diff', 'Diff')}
                </button>
              )}
            </div>
          </div>
        );
      })}
      {open && (
        <OutputDiffDialog
          kind={open.card.kind}
          refId={open.card.refId}
          title={open.card.title}
          initialTo={open.card.version}
          // Comparing is the dialog's default (it picks the parent itself);
          // pinning both ends to one version is what makes `Open` a single pane.
          initialFrom={open.compare ? undefined : open.card.version}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
};

export const InboxNodeView: React.FC<NodeProps<InboxNode>> = ({ node }) => {
  const { t } = useTranslation();
  // Three things arrive through one inbox and read very differently: a
  // sub-agent's answer, a wake-up that fired, and a person steering.
  const result = node.result;
  const wakeup = node.source?.kind === 'schedule';
  // A sub-agent result is only ever filed by the background worker (a
  // foreground child answers in-line), so the row can say so outright — and
  // its own verdict has to travel with it, exactly as on the card.
  const failed = !!result && childState(result.status, 'async') === 'failed';
  const label = result
    ? t('trajectory.inboxSubagent', 'Sub-agent result · {{type}} (Background) · {{verdict}} · read before step {{n}}', {
        type: result.subagentType,
        verdict: failed ? t('subagent.failed', 'Failed') : t('subagent.done', '✓ Done'),
        n: node.step ?? '?',
      })
    : wakeup
      ? t('trajectory.inboxWakeup', 'Wake-up · set by {{who}} · read before step {{n}}', { who: node.source?.createdBy ?? 'user', n: node.step ?? '?' })
      : t('trajectory.inboxClaimed', { kind: node.inboxKind });
  return (
    <div className={`rounded-md ${failed ? 'bg-danger-soft' : 'bg-ok-soft'}`} data-testid="traj-inbox">
      <Row className={failed ? 'text-danger' : 'text-ink-200'}>
        <Inbox size={12} className={`shrink-0 ${failed ? 'text-danger' : wakeup ? 'text-info' : 'text-ok'}`} />
        <span className="truncate">{label}</span>
        {node.step !== null && !result && !wakeup && (
          <span className="ml-auto shrink-0 text-[11px] text-ink-600">{t('trajectory.beforeStep', { n: node.step })}</span>
        )}
      </Row>
      {result && result.summary && (
        <div className="px-2.5 pb-1.5 pl-7 text-[11px] text-ink-400" data-testid="traj-inbox-summary">
          {result.summary}
        </div>
      )}
    </div>
  );
};

/**
 * A wake-up the AGENT set on itself (harness 2b-2 §5-2). Cancelling is a
 * DELETE that can fail, and a cancel that silently did nothing is worse than
 * no button — the failure gets its own line.
 */
export const ScheduleNodeView: React.FC<NodeProps<ScheduleNode>> = ({ node }) => {
  const { t } = useTranslation();
  const [cancelled, setCancelled] = React.useState(false);
  const [failed, setFailed] = React.useState(false);
  const [pending, setPending] = React.useState(false);
  // Compact in the row, full in the tooltip: at thread width the absolute
  // timestamp and the note were both `truncate` and BOTH collapsed to
  // ellipses, which left the row with no information at all (Task 7a defect
  // 8b). Shortening a value is only safe when the full one stays reachable.
  const when = fmtWhenCompact(node.fireAt);
  const whenFull = fmtWhen(node.fireAt);
  const cancel = async (): Promise<void> => {
    if (pending || cancelled) return;
    setPending(true);
    setFailed(false);
    try {
      await schedulesService.remove(node.scheduleId);
      setCancelled(true);
    } catch (err) {
      console.error('[ScheduleNodeView] cancel failed', err);
      setFailed(true);
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="rounded-md bg-info-soft/50" data-testid="traj-schedule" data-cancelled={cancelled ? 'true' : 'false'}>
      <Row className="items-start text-info">
        <Clock size={12} className="mt-0.5 shrink-0" />
        <span data-testid="traj-schedule-at" title={whenFull} className="shrink-0">
          {t('schedule.agentSet', 'Agent scheduled a wake-up · {{at}}', { at: when })}
        </span>
        {node.note && (
          <span data-testid="traj-schedule-note" title={node.note} className="min-w-0 break-words text-ink-400">
            {node.note}
          </span>
        )}
        {!cancelled && (
          <button
            type="button"
            data-testid="traj-schedule-cancel"
            disabled={pending}
            onClick={() => void cancel()}
            className="ml-auto shrink-0 underline decoration-dotted disabled:opacity-50"
          >
            {t('schedule.cancel', 'Cancel')}
          </button>
        )}
      </Row>
      {failed && (
        <div className="px-2.5 pb-1.5 pl-7 text-[11px] text-danger" data-testid="traj-schedule-cancel-error">
          {t('schedule.cancelFailed', 'Could not cancel that wake-up.')}
        </div>
      )}
    </div>
  );
};

export const BudgetNodeView: React.FC<NodeProps<BudgetNode>> = ({ node }) => {
  const { t } = useTranslation();
  const over = node.action === 'halt';
  return (
    <Row className={`rounded-md ${over ? 'bg-danger-soft text-danger' : 'bg-warn-soft text-warn'}`} testId="traj-budget">
      <Wallet size={12} className="shrink-0" />
      <span className="truncate">
        {over ? t('trajectory.budgetHalt', { pct: node.pct ?? 100 }) : t('trajectory.budgetWarn', { pct: node.pct ?? 80 })}
      </span>
      {node.spentCents !== null && node.budgetCents !== null && (
        <span className="ml-auto shrink-0 tabular-nums text-[11px]">
          {fmtCents(node.spentCents)} / {fmtCents(node.budgetCents)}
        </span>
      )}
    </Row>
  );
};

export const TurnEndNodeView: React.FC<NodeProps<TurnEndNode>> = ({ node }) => {
  const { t } = useTranslation();
  const bad = !['completed', 'awaiting_approval'].includes(node.reason);
  return (
    <Row className={`border-t border-ink-800 ${bad ? 'text-warn' : 'text-ink-500'}`} testId="traj-turn-end">
      <span className="truncate">{t('trajectory.turnEnd', { reason: node.reason })}</span>
      <span className="ml-auto shrink-0 tabular-nums text-[11px] text-ink-600">
        {t('trajectory.steps', { count: node.steps })}
        {node.costCents !== null ? ` · ${fmtCents(node.costCents)}` : ''}
      </span>
    </Row>
  );
};

export const DeniedNodeView: React.FC<NodeProps<DeniedNode>> = ({ node }) => {
  const { t } = useTranslation();
  return (
    <Row className="rounded-md bg-danger-soft text-danger" testId="traj-denied">
      <ShieldOff size={12} className="shrink-0" />
      <span className="truncate">{t('trajectory.denied', { tool: node.tool })}</span>
      <span className="ml-auto shrink-0 truncate text-[11px] opacity-80">{node.reason}</span>
    </Row>
  );
};

export const ErrorNodeView: React.FC<NodeProps<ErrorNode>> = ({ node }) => (
  <Row className="text-danger" testId="traj-error">
    <AlertTriangle size={12} className="shrink-0" />
    <span className="truncate">{node.text}</span>
  </Row>
);

registerTrajectoryNode('user', UserNodeView);
registerTrajectoryNode('step', StepNodeView);
registerTrajectoryNode('inbox', InboxNodeView);
registerTrajectoryNode('schedule', ScheduleNodeView);
registerTrajectoryNode('budget', BudgetNodeView);
registerTrajectoryNode('turn_end', TurnEndNodeView);
registerTrajectoryNode('denied', DeniedNodeView);
registerTrajectoryNode('error', ErrorNodeView);
