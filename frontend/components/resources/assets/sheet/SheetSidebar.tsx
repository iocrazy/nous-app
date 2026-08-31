// frontend/components/resources/assets/sheet/SheetSidebar.tsx
//
// The sheet's right column: the action stack, Details, and Used In.
//
// Two deliberate absences, both recorded rather than quietly skipped:
//
//  * SEND TO CANVAS / SEND TO AGENT are DISABLED with a title naming the phase
//    that brings them (P4 / P5). A live button that does nothing is the silent
//    no-op this repo keeps re-learning; a disabled one with a reason is a
//    promise the user can read.
//  * GENERATION HISTORY IS NOT RENDERED. It would be "the generations this
//    asset produced", i.e. `generated_media` filtered by `source_asset_id` -
//    and `GET /api/v1/generated` has no such filter (its query params are
//    state / origin_kind / project_id / media_kind / model / since / cursor /
//    limit). Showing the scope's whole inbox under this asset's name would be
//    a claim that is not true, so the panel is absent until the backend filter
//    exists. Adding it is out of this task's scope by the brief.

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
import type { AssetLoadoutRow, AssetRowDetail } from '../../../../services/assetsService';
import { typeSingularKey } from '../assetTypeMeta';
import { canvasKindFor } from './assetSheetModel';

export interface SheetSidebarProps {
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
  onOpenCanvas: (canvasId: string) => void;
  onError: (err: unknown) => void;
}

const ACTION =
  'flex w-full items-center gap-2 rounded-lg border border-line-strong bg-card px-2.5 py-1.5 ' +
  'text-xs font-medium text-content-2 transition-colors hover:bg-island-2 ' +
  'disabled:cursor-not-allowed disabled:opacity-50';

export const SheetSidebar: React.FC<SheetSidebarProps> = ({
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
  onError,
}) => {
  const { t } = useTranslation();

  return (
    <aside data-testid="sheet-sidebar" className="flex w-64 shrink-0 flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <button
          type="button"
          data-testid="send-to-canvas"
          disabled
          title={t('assets.sheet.arrivesWithP4', 'Arrives with P4')}
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

      <section data-testid="used-in-panel" className="flex flex-col gap-1.5">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-content-4">
          {t('assets.sheet.usedIn', 'Used In')}
        </h3>
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
                className="max-w-full truncate rounded-full border border-line-strong px-2 py-0.5 text-[11px] text-content-3"
              >
                {projectNames[projectId] ?? projectId}
              </li>
            ))}
          </ul>
        )}
        {/* Stated, not omitted: a Used In panel that lists projects only,
            with no note, reads as "this asset is on no canvas". */}
        <p className="text-[11px] text-content-4">
          {t('assets.sheet.canvasUsageLater', 'Canvas usage arrives with P4')}
        </p>
      </section>
    </aside>
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
