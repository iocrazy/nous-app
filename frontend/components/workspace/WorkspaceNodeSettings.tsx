/**
 * WorkspaceNodeSettings — the Settings module's "Node Config" tab (IA
 * redesign Task 10, spec §7). Lets the project/episode owner switch between
 * episodes, reassign that episode's own "集负责人" (Task 6, `episodes.owner_id`),
 * and edit any node's owner/members/schedule/brief facts — the same PATCH
 * surface `EpisodeNodeCard` (Task 9) exposes inline on the Overview
 * accordion, reachable here as a dedicated, deep-linkable destination
 * (`?module=settings&tab=nodes&ep=&node=`, wired by `ProjectWorkspace`).
 *
 * Deviates from the brief's `workflow: ProjectWorkflow | null` prop: this
 * panel must be able to show ANY episode's node strip, not just whichever
 * one `ProjectWorkspace`'s own `currentEpisodeId` happens to be on — so it
 * fetches its OWN workflow instance (`useProjectWorkflow`, the exact hook
 * `ProjectWorkspace` itself uses) keyed off a local `selectedEpisodeId`
 * state, entirely independent of the main workspace's episode selection.
 * Switching episodes here does NOT call back into
 * `ProjectWorkspace.handleEpisodeChange` — it never touches the main
 * workspace's current-episode state, `view`/`scene`/`shot` params, or
 * localStorage. `onEpisodeChange`/`onNodeChange` are optional so a host CAN
 * mirror the selection into the URL `ep=`/`node=` (refresh-safe deep link)
 * without this component knowing anything about routing.
 *
 * The node form reuses `CurrentNodeCard`'s editing idiom directly
 * (`OwnerPicker` owner/members + `DateTimePopover` schedule + `BriefField`
 * brief, each `disabled` when `!canEditFor(selectedEpisodeId)`) rather than
 * `EpisodeNodeCard`'s compact trigger+menu — this is a fuller settings
 * surface, not an accordion summary card. The episode-owner row instead
 * reuses `EpisodeNodeCard`'s trigger+`OwnerCandidateList` pattern (agents
 * excluded — `episodes.owner_id` is a human-only FK, unlike a node owner
 * which may be an agent).
 *
 * `WorkflowStrip`'s `currentNodeId` prop is repurposed here to mean "the
 * node currently open in this form" rather than "the workflow's live
 * cursor" — there is no separate "selected" visual state on that component,
 * and its accent-ring styling reads naturally as "this one" in a picker
 * context. Skipped nodes render dashed/struck-through already (own styling);
 * `handleSelectNode` additionally no-ops a click on one so it can never be
 * opened for editing.
 */

import { useEffect, useState, type FC, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, FileCheck2, User } from 'lucide-react';
import { useProjectWorkflow } from '../../hooks/useProjectWorkflow';
import { updateProjectNode } from '../../services/workflowService';
import { updateEpisode } from '../../services/projectsService';
import { ApiError } from '../../services/apiClient';
import { useOptionalToast } from '../Toast';
import { WorkflowStrip } from '../workflow/WorkflowStrip';
import { OwnerPicker, type AgentOption, type PersonOption } from '../workflow/OwnerPicker';
import { OwnerCandidateList } from '../workflow/OwnerCandidateList';
import { DateTimePopover } from '../common/DateTimePopover';
import { BriefField } from '../workflow/BriefField';
import type {
  EpisodeProgress,
  ProjectNodePatch,
  ProjectStageNode,
  WorkflowMemberRef,
} from '../../types';

export interface WorkspaceNodeSettingsProps {
  projectId: string;
  episodes: EpisodeProgress[];
  /** URL `ep=` — preselects this episode when it still exists in `episodes`. */
  initialEpisodeId: string | null;
  /** URL `node=` — preselects this node once its episode's workflow loads
   * (only takes effect if the node actually belongs to it). */
  initialNodeId: string | null;
  /** Project owner OR the given episode's own owner (same predicate Task 9
   * computes for `EpisodeNodeCard`, see `ProjectWorkspace.canEditNodeConfig`). */
  canEditFor: (episodeId: string) => boolean;
  /** Only the project owner may assign/clear an episode's owner. */
  canAssignEpisodeOwner: boolean;
  people: PersonOption[];
  agents?: AgentOption[];
  /** Fired on every episode-switcher pick — does not itself touch the URL. */
  onEpisodeChange?: (episodeId: string) => void;
  /** Fired on every node-strip pick (never for a skipped node — those are
   * unselectable). */
  onNodeChange?: (nodeId: string) => void;
  /** Bubbles a fresh `owner_id` after a successful episode-owner PATCH so a
   * host can refresh its own `episodes` list (e.g. `canEditFor` elsewhere
   * derives from `EpisodeProgress.owner_id`). */
  onEpisodeOwnerChanged?: (episodeId: string, ownerId: string | null) => void;
  /** Fired after a successful node PATCH (owner/members/schedule/brief),
   * carrying the episode it belongs to. Task 10 修复轮1 (Important #2):
   * this panel owns its OWN `useProjectWorkflow` instance (see the file-doc
   * — deliberately independent of the host's `currentEpisodeId`), so a PATCH
   * here reloads only that instance; a host also holding a separate instance
   * for the SAME episode (`ProjectWorkspace`'s Overview/top-bar one) would
   * otherwise go stale until its own next incidental refetch. The host
   * decides whether the patched episode is "the one it's watching" — this
   * callback carries `episodeId` for exactly that comparison. */
  onNodePatched?: (episodeId: string) => void;
}

const Row: FC<{ label: string; children: ReactNode }> = ({ label, children }) => (
  <div className="grid grid-cols-[6rem_1fr] items-start gap-3 py-1.5">
    <span className="pt-1.5 text-[11px] uppercase tracking-wider text-ink-600">{label}</span>
    <div className="min-w-0">{children}</div>
  </div>
);

export function WorkspaceNodeSettings({
  projectId,
  episodes,
  initialEpisodeId,
  initialNodeId,
  canEditFor,
  canAssignEpisodeOwner,
  people,
  agents = [],
  onEpisodeChange,
  onNodeChange,
  onEpisodeOwnerChanged,
  onNodePatched,
}: WorkspaceNodeSettingsProps) {
  const { t } = useTranslation();
  const toast = useOptionalToast();

  const sortedEpisodes = [...episodes].sort((a, b) => a.sort_order - b.sort_order);
  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(
    initialEpisodeId && episodes.some((e) => e.episode_id === initialEpisodeId)
      ? initialEpisodeId
      : (sortedEpisodes[0]?.episode_id ?? null),
  );
  const [episodePickerOpen, setEpisodePickerOpen] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(initialNodeId);
  const [nodeSaving, setNodeSaving] = useState(false);
  const [scheduleAnchor, setScheduleAnchor] = useState<HTMLElement | null>(null);
  const [episodeOwnerMenuOpen, setEpisodeOwnerMenuOpen] = useState(false);
  const [episodeOwnerSaving, setEpisodeOwnerSaving] = useState(false);

  const { workflow, reload, patchNodeLocally } = useProjectWorkflow(projectId, selectedEpisodeId);

  // Task 10 修复轮1: a COLD deep-link (`?module=settings&tab=nodes&ep=&node=`
  // opened fresh, not clicked into from an already-loaded Overview) can mount
  // this component before the host's `episodes` list has fetched — the
  // `useState` initializer above only runs ONCE, at mount, so it falls
  // through to `sortedEpisodes[0] ?? null` against an EMPTY array and
  // permanently strands `selectedEpisodeId` at `null` (nothing re-derives it
  // once `episodes` actually arrives as a prop update). Since
  // `useProjectWorkflow` skips its fetch entirely while `episodeId` is
  // `null`, the panel would sit blank forever. Resolve it once, the same way
  // the initializer would have, the moment `episodes` stops being empty —
  // only while still unresolved, so this never fights a real user pick.
  useEffect(() => {
    if (selectedEpisodeId) return;
    if (episodes.length === 0) return;
    const sorted = [...episodes].sort((a, b) => a.sort_order - b.sort_order);
    const initial =
      initialEpisodeId && episodes.some((e) => e.episode_id === initialEpisodeId)
        ? initialEpisodeId
        : (sorted[0]?.episode_id ?? null);
    setSelectedEpisodeId(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deliberately narrow: only resolves the initial cold-mount case (selectedEpisodeId still null), not every `episodes` refresh
  }, [episodes]);

  // Re-pick a valid node whenever the workflow (episode) changes — the
  // previously-selected id may belong to a different episode's node list.
  useEffect(() => {
    if (!workflow) return;
    if (selectedNodeId && workflow.nodes.some((n) => n.id === selectedNodeId)) return;
    setSelectedNodeId(workflow.current_node_id ?? workflow.nodes[0]?.id ?? null);
  }, [workflow, selectedNodeId]);

  const currentEpisode = episodes.find((e) => e.episode_id === selectedEpisodeId) ?? null;
  const selectedNode = workflow?.nodes.find((n) => n.id === selectedNodeId) ?? null;
  const canEdit = selectedEpisodeId ? canEditFor(selectedEpisodeId) : false;

  const handleSelectEpisode = (episodeId: string) => {
    setSelectedEpisodeId(episodeId);
    setSelectedNodeId(null); // resolved by the effect above once the new workflow loads
    setEpisodePickerOpen(false);
    onEpisodeChange?.(episodeId);
  };

  const handleSelectNode = (node: ProjectStageNode) => {
    if (node.skipped || node.status === 'skipped') return; // unselectable
    setSelectedNodeId(node.id);
    onNodeChange?.(node.id);
  };

  const patchNode = async (patch: ProjectNodePatch) => {
    if (!selectedNode || !selectedEpisodeId) return;
    const episodeId = selectedEpisodeId; // capture — see onNodePatched below
    setNodeSaving(true);
    patchNodeLocally(selectedNode.id, patch);
    try {
      await updateProjectNode(projectId, selectedNode.id, patch);
      await reload();
      onNodePatched?.(episodeId);
    } catch (err) {
      console.error('[WorkspaceNodeSettings] node patch failed', err);
      await reload();
      const forbidden =
        err instanceof ApiError &&
        err.status === 403 &&
        (err.details as { code?: string } | undefined)?.code === 'node_config_forbidden';
      toast?.addToast(
        forbidden ? t('projects.nodeCard.forbidden', 'Only the project or episode owner can edit this') : t('common.error'),
        'error',
      );
    } finally {
      setNodeSaving(false);
    }
  };

  const patchEpisodeOwner = async (ownerId: string | null) => {
    if (!selectedEpisodeId) return;
    setEpisodeOwnerMenuOpen(false);
    setEpisodeOwnerSaving(true);
    try {
      await updateEpisode(selectedEpisodeId, { owner_id: ownerId });
      onEpisodeOwnerChanged?.(selectedEpisodeId, ownerId);
    } catch (err) {
      console.error('[WorkspaceNodeSettings] episode owner patch failed', err);
      const forbidden =
        err instanceof ApiError &&
        err.status === 403 &&
        (err.details as { code?: string } | undefined)?.code === 'episode_owner_forbidden';
      toast?.addToast(
        forbidden
          ? t('projects.workspace.nodeSettings.episodeOwnerForbidden', 'Only the project owner can assign the episode owner')
          : t('common.error'),
        'error',
      );
    } finally {
      setEpisodeOwnerSaving(false);
    }
  };

  const isEpisodeOwnerSelected = (ref: WorkflowMemberRef): boolean =>
    !!ref.user_id && ref.user_id === currentEpisode?.owner_id;

  const episodeOwnerName = currentEpisode?.owner_id
    ? (people.find((p) => p.id === currentEpisode.owner_id)?.name ?? t('projects.workflow.genericMember'))
    : t('projects.workflow.unassigned');

  const briefReadOnly =
    !canEdit ||
    nodeSaving ||
    !selectedNode ||
    selectedNode.status === 'done' ||
    selectedNode.status === 'skipped' ||
    selectedNode.skipped;

  return (
    <div data-testid="workspace-node-settings" className="space-y-4">
      <div className="relative inline-block">
        <button
          type="button"
          data-testid="node-settings-episode-switch"
          onClick={() => setEpisodePickerOpen((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-[13px] font-medium text-ink-100 transition hover:border-line-strong"
        >
          {currentEpisode?.title ?? t('projects.workspace.nodeSettings.selectEpisode', 'Select episode')}
          <ChevronDown size={14} className="text-ink-500" />
        </button>
        {episodePickerOpen && (
          <div className="absolute left-0 top-full z-30 mt-1 max-h-64 w-56 overflow-y-auto rounded-lg border border-line-strong bg-island py-1 shadow-2xl">
            {sortedEpisodes.map((ep) => (
              <button
                key={ep.episode_id}
                type="button"
                onClick={() => handleSelectEpisode(ep.episode_id)}
                className="flex w-full items-center px-2.5 py-1.5 text-left text-[13px] text-ink-200 hover:bg-ink-800"
              >
                {ep.title}
              </button>
            ))}
          </div>
        )}
      </div>

      <div
        data-testid="node-settings-episode-owner-row"
        className="rounded-xl border border-line bg-island p-4"
      >
        <div className="grid grid-cols-[6rem_1fr] items-start gap-3">
          <span className="pt-0.5 text-[11px] uppercase tracking-wider text-ink-600">
            {t('projects.workspace.nodeSettings.episodeOwner', 'Episode Owner')}
          </span>
          {canAssignEpisodeOwner ? (
            <div className="relative inline-block">
              <button
                type="button"
                data-testid="node-settings-episode-owner"
                onClick={() => setEpisodeOwnerMenuOpen((v) => !v)}
                disabled={episodeOwnerSaving}
                className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[13px] text-ink-200 transition hover:border hover:border-line hover:bg-island-2 disabled:opacity-60"
              >
                <User size={12} />
                {episodeOwnerName}
                <ChevronDown size={11} className="text-ink-600" />
              </button>
              {episodeOwnerMenuOpen && (
                <div className="absolute left-0 top-full z-30 mt-1 max-h-64 w-48 overflow-y-auto rounded-lg border border-line-strong bg-island py-1 shadow-2xl">
                  {currentEpisode?.owner_id && (
                    <button
                      type="button"
                      onClick={() => void patchEpisodeOwner(null)}
                      className="flex w-full items-center px-2.5 py-1.5 text-[13px] text-ink-500 hover:bg-ink-800"
                    >
                      {t('projects.nodeCard.unassign', 'Unassign')}
                    </button>
                  )}
                  <OwnerCandidateList
                    people={people}
                    agents={[]}
                    isSelected={isEpisodeOwnerSelected}
                    onSelect={(ref) => void patchEpisodeOwner(ref.user_id ?? null)}
                    peopleLabel={t('projects.nodeCard.people', 'People')}
                    agentsLabel={t('projects.nodeCard.agents', 'Agents')}
                    emptyLabel={t('projects.nodeCard.noCandidates', 'No candidates')}
                  />
                </div>
              )}
            </div>
          ) : (
            <span
              data-testid="node-settings-episode-owner"
              className="inline-flex items-center gap-1.5 text-[13px] text-ink-300"
            >
              <User size={12} /> {episodeOwnerName}
            </span>
          )}
        </div>
      </div>

      {workflow && workflow.nodes.length > 0 && (
        <WorkflowStrip nodes={workflow.nodes} currentNodeId={selectedNodeId} onSelectNode={handleSelectNode} />
      )}

      {selectedNode && (
        <div
          data-testid="node-settings-form"
          data-node-id={selectedNode.id}
          className="rounded-xl border border-line bg-island p-4"
        >
          <h3 className="mb-2 text-sm font-semibold text-ink-100">{selectedNode.name}</h3>

          <Row label={t('projects.workflow.owner')}>
            <OwnerPicker
              mode="owner"
              people={people}
              agents={agents}
              ownerUserId={selectedNode.owner_user_id}
              ownerAgentId={selectedNode.owner_agent_id}
              disabled={!canEdit || nodeSaving}
              placeholder={t('projects.workflow.unassigned')}
              onOwnerChange={(ref) =>
                void patchNode({ owner_user_id: ref?.user_id ?? null, owner_agent_id: ref?.agent_id ?? null })
              }
            />
          </Row>

          <Row label={t('projects.workflow.members')}>
            <OwnerPicker
              mode="members"
              people={people}
              agents={agents}
              members={selectedNode.members}
              disabled={!canEdit || nodeSaving}
              onMembersChange={(members) => void patchNode({ members })}
            />
          </Row>

          <Row label={t('projects.workflow.schedule')}>
            <button
              type="button"
              data-testid="node-settings-schedule"
              disabled={!canEdit || nodeSaving}
              onClick={(e) => setScheduleAnchor(e.currentTarget)}
              className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[13px] text-ink-200 transition hover:border-line-strong disabled:cursor-not-allowed disabled:opacity-50"
            >
              {selectedNode.planned_start ?? '—'} → {selectedNode.planned_due ?? '—'}
            </button>
          </Row>
          <DateTimePopover
            anchorEl={scheduleAnchor}
            start={selectedNode.planned_start}
            end={selectedNode.planned_due}
            onChange={(start, end) => {
              setScheduleAnchor(null);
              void patchNode({ planned_start: start, planned_due: end });
            }}
            onClose={() => setScheduleAnchor(null)}
          />

          {(selectedNode.deliverable_label || selectedNode.deliverable_required) && (
            <Row label={t('projects.workflow.deliverable')}>
              <span className="inline-flex items-center gap-2 text-[13px] text-ink-300">
                <FileCheck2 size={13} className="shrink-0 text-ink-500" />
                <span className="truncate">{selectedNode.deliverable_label || '—'}</span>
                <span
                  className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] ${
                    (selectedNode.deliverable_file_count ?? 0) > 0 ? 'bg-ok-soft text-ok' : 'bg-ink-800 text-ink-500'
                  }`}
                >
                  {t('projects.workflow.deliverables.filesFiled', { count: selectedNode.deliverable_file_count ?? 0 })}
                </span>
              </span>
            </Row>
          )}

          <Row label={t('projects.workflow.brief.label')}>
            <BriefField
              value={selectedNode.brief ?? ''}
              disabled={briefReadOnly}
              placeholder={t('projects.workflow.brief.placeholder')}
              onSave={(next) => void patchNode({ brief: next })}
              testId="node-settings-brief"
            />
          </Row>
        </div>
      )}
    </div>
  );
}

export default WorkspaceNodeSettings;
