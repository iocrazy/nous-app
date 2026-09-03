// frontend/components/resources/assets/sheet/SheetSidebar.tsx
//
// The sheet's right column: the action stack, Details, and Used In.
//
// SEND TO CANVAS is live as of P4 Task 6 — it opens
// `SendAssetToCanvasDialog`, which appends a reference card to a canvas the
// user picks. SEND TO AGENT is still DISABLED with a title naming the phase
// that brings it (P5): a live button that does nothing is the silent no-op
// this repo keeps re-learning; a disabled one with a reason is a promise the
// user can read.
//
// USED IN answers two questions since P4 Task 8: which PROJECTS reference the
// asset (`project_ids`, P2) and which CANVASES place a card for it
// (`used_in.canvases`, shipped by P4 Task 1). The panel used to carry a
// standing note saying canvas usage would arrive with P4; that note is gone,
// because the answer is now real and a permanent "later" line is how a
// shipped surface stays invisible.
//
// GENERATION HISTORY now sits under Used In. Task 7 left it out because
// `GET /generated` had no `source_asset_id` filter and the only thing it could
// have shown was the scope's whole inbox under this asset's name; Task 8 added
// the filter, so the panel asks a question it can actually answer. See
// `GenerationHistoryPanel`.

import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Bot,
  Copy,
  ExternalLink,
  Frame,
  Loader2,
  Trash2,
} from 'lucide-react';

import { createCanvas } from '../../../../features/canvas-core/services/canvasService';
import type { Project } from '../../../../types';
import type {
  AssetLoadoutRow,
  AssetRowDetail,
  UsedInCanvasRef,
} from '../../../../services/assetsService';
import { shortProjectLabel } from '../AssetCard';
import { typeSingularKey } from '../assetTypeMeta';
import { canvasKindFor } from './assetSheetModel';
import { GenerationHistoryPanel } from './GenerationHistoryPanel';
import { SendAssetToCanvasDialog } from './SendAssetToCanvasDialog';

export interface SheetSidebarProps {
  /** Null until the resources context resolves one; the history panel is the
   *  only thing here that fetches, so it simply waits. */
  scopeId: string | null;
  detail: AssetRowDetail;
  loadout: AssetLoadoutRow | null;
  readOnly: boolean;
  /**
   * The projects of THIS asset's scope, already fetched by the page (team-scoped
   * via `fetchProjects({teamId})`). Passed down rather than re-fetched: an
   * unscoped `GET /projects` returns every project the user can see, and
   * `_require_asset_in_project_scope` 404s the moment the chosen project's team
   * does not hold the asset - so an unscoped picker offers choices that
   * dead-end on an error toast.
   */
  projects: Project[];
  /** THREE states, never two. "Not yet known", "the fetch failed" and "this
   *  workspace has none" each want a different sentence; folding any two of
   *  them together states something untrue about the workspace. */
  projectsLoading: boolean;
  projectsFailed: boolean;
  /** Project id -> name for the Used In chips. */
  projectNames: Record<string, string>;
  onCopyLoadoutPrompt: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  busy: boolean;
  /**
   * Go to a canvas. `nodeId` is passed by Send To Canvas so the page can
   * navigate with `?node=<id>` — the canvas's own latch then selects and
   * centres the card that was just added. Without it the user lands on a
   * board and has to hunt for what they sent.
   */
  onOpenCanvas: (canvasId: string, nodeId?: string) => void;
  /** Navigate to the Generated inbox, filtered to unreviewed. */
  onOpenInbox: () => void;
  onError: (err: unknown) => void;
}

const ACTION =
  'flex w-full items-center gap-2 rounded-lg border border-line-strong bg-card px-2.5 py-1.5 ' +
  'text-xs font-medium text-content-2 transition-colors hover:bg-island-2 ' +
  'disabled:cursor-not-allowed disabled:opacity-50';

export const SheetSidebar: React.FC<SheetSidebarProps> = ({
  scopeId,
  detail,
  loadout,
  readOnly,
  projects,
  projectsLoading,
  projectsFailed,
  projectNames,
  onCopyLoadoutPrompt,
  onDuplicate,
  onDelete,
  busy,
  onOpenCanvas,
  onOpenInbox,
  onError,
}) => {
  const { t } = useTranslation();
  const [sendOpen, setSendOpen] = useState(false);

  return (
    <aside data-testid="sheet-sidebar" className="flex w-64 shrink-0 flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <button
          type="button"
          data-testid="send-to-canvas"
          onClick={() => setSendOpen(true)}
          className={ACTION}
        >
          <Frame size={13} aria-hidden="true" />
          {t('assets.sheet.sendToCanvas', 'Send To Canvas')}
        </button>
        <button
          type="button"
          data-testid="send-to-agent"
          disabled
          title={t('assets.sheet.arrivesWithP5', 'Arrives with P5')}
          className={ACTION}
        >
          <Bot size={13} aria-hidden="true" />
          {t('assets.sheet.sendToAgent', 'Send To Agent')}
        </button>
        <button
          type="button"
          data-testid="copy-loadout-prompt"
          onClick={onCopyLoadoutPrompt}
          className={ACTION}
        >
          <Copy size={13} aria-hidden="true" />
          {t('assets.sheet.copyLoadoutPrompt', 'Copy Loadout Prompt')}
        </button>
        <button
          type="button"
          data-testid="duplicate-asset"
          disabled={busy}
          onClick={onDuplicate}
          className={ACTION}
        >
          {busy ? (
            <Loader2 size={13} className="animate-spin" aria-hidden="true" />
          ) : (
            <Copy size={13} aria-hidden="true" />
          )}
          {t('assets.sheet.duplicate', 'Duplicate')}
        </button>
        {/* Absent on a preset: the server refuses with 403
            `system_preset_readonly`, and the whole sheet is already in
            read-only mode saying so. */}
        {!readOnly && (
          <button
            type="button"
            data-testid="delete-asset"
            disabled={busy}
            onClick={onDelete}
            className={`${ACTION} hover:text-danger`}
          >
            <Trash2 size={13} aria-hidden="true" />
            {t('assets.sheet.delete', 'Delete')}
          </button>
        )}

        <OpenInCanvasLink
          detail={detail}
          projects={projects}
          projectsLoading={projectsLoading}
          projectsFailed={projectsFailed}
          onOpenCanvas={onOpenCanvas}
          onError={onError}
        />
      </div>

      <section data-testid="details-panel" className="flex flex-col gap-1.5">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-content-4">
          {t('assets.sheet.details', 'Details')}
        </h3>
        <DetailRow label={t('assets.sheet.type', 'Type')}>
          {t(typeSingularKey(detail.asset_type), detail.asset_type)}
        </DetailRow>
        {detail.subtype && (
          <DetailRow label={t('assets.sheet.subtype', 'Subtype')}>{detail.subtype}</DetailRow>
        )}
        <DetailRow label={t('assets.sheet.source', 'Source')}>
          {t(`assets.source.${detail.source}`, detail.source)}
        </DetailRow>
        <DetailRow label={t('assets.sheet.updated', 'Updated')}>
          {new Date(detail.updated_at).toLocaleString()}
        </DetailRow>
        {/* `tags` is an OBJECT of group -> values on the wire, not a flat
            array; flattening it for display keeps the group name visible. */}
        {Object.entries(detail.tags ?? {}).map(([group, values]) => (
          <DetailRow key={group} label={group}>
            {Array.isArray(values) ? values.join(', ') : String(values)}
          </DetailRow>
        ))}
        <DetailRow label={t('assets.sheet.assetId', 'Asset ID')}>
          <span className="tabular-nums">{detail.id}</span>
        </DetailRow>
        {loadout && (
          <DetailRow label={t('assets.sheet.activeLoadout', 'Active Loadout')}>
            {loadout.name}
          </DetailRow>
        )}
      </section>

      <section data-testid="used-in-panel" className="flex flex-col gap-2">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-content-4">
          {t('assets.sheet.usedIn', 'Used In')}
        </h3>

        <div className="flex flex-col gap-1">
          <p className="text-[10px] font-medium uppercase tracking-wide text-content-4">
            {t('assets.sheet.usedInProjects', 'Projects')}
          </p>
          {detail.project_ids.length === 0 ? (
            <p className="text-[12px] text-content-4">
              {t('assets.sheet.noProjects', 'No Projects Yet')}
            </p>
          ) : (
            <ul className="flex flex-wrap gap-1">
              {detail.project_ids.map((projectId) => (
                <li
                  key={projectId}
                  data-testid="used-in-project"
                  data-project-id={projectId}
                  // Same fallback rule as `AssetCard`'s chips, from the same
                  // helper: a project whose name this page could not resolve is
                  // still a project the asset is used in, but a raw 15-digit
                  // Snowflake is not a label — the full id lives in the tooltip.
                  title={projectNames[projectId] ?? projectId}
                  className="max-w-full truncate rounded-full border border-line-strong px-2 py-0.5 text-[11px] text-content-3"
                >
                  {projectNames[projectId] ?? shortProjectLabel(projectId)}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* `used_in` is OPT-IN on `GET /assets/{id}` and this page is the one
            caller that asks for it, so the `?.` here is a real branch, not
            defensiveness: an `undefined` means the aggregate was not run.
            Rendering it as an empty list is right for this panel — the sheet
            always asks, so reaching here without one is a bug in this file's
            own fetch, and "no canvases" is the least wrong thing to draw while
            it is. It must NOT become the default in the service layer, where
            "nobody asked" and "used nowhere" have to stay distinguishable. */}
        <UsedInCanvases
          canvases={detail.used_in?.canvases ?? []}
          projectNames={projectNames}
          onOpenCanvas={onOpenCanvas}
        />
        {/* `used_in.storyboards` is on the wire and always empty — the
            storyboard side has no ref mirror yet. Nothing is rendered for it
            on purpose: a "Storyboards: none" row would state a fact this
            release cannot actually check. */}
      </section>

      {scopeId && (
        <GenerationHistoryPanel
          scopeId={scopeId}
          assetId={detail.id}
          onOpenInbox={onOpenInbox}
        />
      )}

      {sendOpen && (
        <SendAssetToCanvasDialog
          detail={detail}
          loadoutId={loadout?.id ?? null}
          projects={projects}
          projectsLoading={projectsLoading}
          projectsFailed={projectsFailed}
          onClose={() => setSendOpen(false)}
          onDone={(canvasId, nodeId) => {
            setSendOpen(false);
            onOpenCanvas(canvasId, nodeId);
          }}
        />
      )}
    </aside>
  );
};

// --- Used In: canvases ------------------------------------------------------

interface UsedInCanvasesProps {
  canvases: UsedInCanvasRef[];
  projectNames: Record<string, string>;
  onOpenCanvas: (canvasId: string, nodeId?: string) => void;
}

/**
 * The canvases that place a card for this asset (P4).
 *
 * Each row opens the canvas at its FIRST card (`?node=<id>` — the canvas's own
 * latch selects and centres it), so the link lands on the thing the row is
 * about rather than on a board the user then has to hunt through. The server
 * aggregates per canvas, so a board carrying the asset three times is one row;
 * the card count is said out loud when it is more than one, because "Bamboo
 * Sea" alone would understate what removing the asset there would affect.
 *
 * The empty state is a SENTENCE, not an omission: a panel that renders nothing
 * for "no canvases" is indistinguishable from one whose fetch quietly failed.
 */
const UsedInCanvases: React.FC<UsedInCanvasesProps> = ({
  canvases,
  projectNames,
  onOpenCanvas,
}) => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-1">
      <p className="text-[10px] font-medium uppercase tracking-wide text-content-4">
        {t('assets.sheet.usedInCanvases', 'Canvases')}
      </p>
      {canvases.length === 0 ? (
        <p data-testid="used-in-no-canvases" className="text-[12px] text-content-4">
          {t('assets.sheet.noCanvases', 'Not On Any Canvas Yet')}
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {canvases.map((canvas) => (
            <li
              key={canvas.canvas_id}
              data-testid="used-in-canvas"
              data-canvas-id={canvas.canvas_id}
              data-node-count={canvas.node_ids.length}
              className="flex items-baseline justify-between gap-2"
            >
              <button
                type="button"
                data-testid="used-in-canvas-open"
                // `node_ids[0]` may be absent on a row whose nodes were
                // aggregated away by a concurrent save; `undefined` then makes
                // `onOpenCanvas` navigate without `?node=`, which is the right
                // fallback — the board still opens.
                onClick={() => onOpenCanvas(canvas.canvas_id, canvas.node_ids[0])}
                className="min-w-0 truncate text-left text-[12px] text-accent hover:underline"
              >
                {canvas.canvas_name}
              </button>
              <span className="flex shrink-0 items-center gap-1">
                {canvas.node_ids.length > 1 && (
                  <span
                    data-testid="used-in-canvas-count"
                    className="text-[10px] tabular-nums text-content-4"
                  >
                    {t('assets.sheet.canvasCards', {
                      n: canvas.node_ids.length,
                      defaultValue: '{{n}} Cards',
                    })}
                  </span>
                )}
                <span
                  data-testid="used-in-canvas-project"
                  data-project-id={canvas.project_id}
                  className="max-w-[7rem] truncate rounded-full border border-line-strong px-2 py-0.5 text-[10px] text-content-3"
                >
                  {projectNames[canvas.project_id] ?? canvas.project_id}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

const DetailRow: React.FC<{ label: string; children: React.ReactNode }> = ({
  label,
  children,
}) => (
  <div className="flex items-baseline justify-between gap-2 text-[12px]">
    <span className="shrink-0 text-content-4">{label}</span>
    <span className="min-w-0 truncate text-right text-content-2">{children}</span>
  </div>
);

// --- Open in canvas ---------------------------------------------------------

interface OpenInCanvasLinkProps {
  detail: AssetRowDetail;
  /** The scope's projects, from the page. */
  projects: Project[];
  projectsLoading: boolean;
  projectsFailed: boolean;
  onOpenCanvas: (canvasId: string) => void;
  onError: (err: unknown) => void;
}

/**
 * P0 decision 13: create a kind-matched canvas for this asset.
 *
 * A canvas belongs to a PROJECT (`POST /projects/{id}/canvases`), so this needs
 * one. When the asset is already linked to exactly one project that is the
 * answer; when it is linked to several, or none, the user picks - silently
 * choosing the first would put the canvas somewhere they did not ask for, and
 * refusing outright would strand every asset that is not yet on a project.
 *
 * The list is the page's team-scoped one. See `projects` on the props above for
 * why it is not fetched here.
 */
const OpenInCanvasLink: React.FC<OpenInCanvasLinkProps> = ({
  detail,
  projects,
  projectsLoading,
  projectsFailed,
  onOpenCanvas,
  onError,
}) => {
  const { t } = useTranslation();
  const [picking, setPicking] = useState(false);
  const [creating, setCreating] = useState(false);

  const open = useCallback(
    async (projectId: string) => {
      if (creating) return;
      setCreating(true);
      try {
        const canvas = await createCanvas(projectId, {
          name: detail.name,
          kind: canvasKindFor(detail.asset_type),
          asset_id: detail.id,
        });
        setPicking(false);
        onOpenCanvas(String(canvas.id));
      } catch (err) {
        onError(err);
      } finally {
        setCreating(false);
      }
    },
    [creating, detail.name, detail.asset_type, detail.id, onOpenCanvas, onError],
  );

  const only = detail.project_ids.length === 1 ? detail.project_ids[0] : null;

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        data-testid="open-in-canvas"
        disabled={creating}
        onClick={() => (only ? void open(only) : setPicking((v) => !v))}
        className="inline-flex items-center gap-1 self-start text-[11px] font-medium text-accent hover:underline disabled:opacity-50"
      >
        {creating && <Loader2 size={11} className="animate-spin" aria-hidden="true" />}
        {t('assets.sheet.openInCanvas', 'Open In Canvas')}
        <ExternalLink size={10} aria-hidden="true" />
      </button>

      {picking && (
        <div
          data-testid="canvas-project-picker"
          className="rounded-lg border border-line bg-card p-2"
        >
          <p className="mb-1 text-[11px] text-content-3">
            {t('assets.sheet.pickProject', 'Pick A Project For The Canvas')}
          </p>
          {projectsLoading ? (
            <p className="text-[11px] text-content-4">{t('common.loading', 'Loading...')}</p>
          ) : projectsFailed ? (
            <p role="alert" className="text-[11px] text-danger">
              {t('assets.projectsUnavailable', 'Projects unavailable')}
            </p>
          ) : projects.length === 0 ? (
            <p className="text-[11px] text-content-4">
              {t('assets.sheet.noProjectsToPick', 'Create A Project First')}
            </p>
          ) : (
            <ul className="flex max-h-40 flex-col overflow-y-auto">
              {projects.map((project) => (
                <li key={String(project.id)}>
                  <button
                    type="button"
                    data-testid="canvas-project-option"
                    data-project-id={String(project.id)}
                    onClick={() => void open(String(project.id))}
                    className="w-full truncate rounded px-1.5 py-1 text-left text-[12px] text-content-2 hover:bg-island-2"
                  >
                    {project.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};
