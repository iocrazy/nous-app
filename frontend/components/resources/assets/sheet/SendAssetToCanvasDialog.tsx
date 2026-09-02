// frontend/components/resources/assets/sheet/SendAssetToCanvasDialog.tsx
//
// "Send To Canvas" (P4 Task 6) — put THIS asset on a canvas that already
// exists, as an asset reference card.
//
// It is not the same action as "Open In Canvas" one button above, and the
// difference is worth stating because both end on a canvas:
//
//   Open In Canvas  → creates the asset's OWN kind-matched canvas
//                     (`canvases.asset_id` set) and goes there.
//   Send To Canvas  → drops a reference card onto a board the user already
//                     works in, chosen here.
//
// "New Canvas" inside this dialog therefore makes a plain `smart` board named
// after the asset, NOT a second copy of the entity canvas — that one is
// already reachable, and offering it twice under two names would make the two
// buttons indistinguishable.
//
// Failures are typed and each gets its own sentence. "Could not send" for a
// read-only canvas, a lost race and a dead network reads as one unexplained
// refusal, and the user's next move differs in all three.

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Layers, Loader2, Plus, X } from 'lucide-react';

import { useToast } from '../../../Toast';
import { createCanvas, listCanvases } from '../../../../features/canvas-core/services/canvasService';
import {
  sendAssetToCanvas,
  type SendFailureReason,
} from '../../../../features/canvas-core/services/sendAssetToCanvas';
import type { Canvas } from '../../../../features/canvas-core/types';
import type { AssetRowDetail } from '../../../../services/assetsService';
import type { Project } from '../../../../types';

/** Every typed reason, plus the two this component owns (creating the new
 *  canvas, listing a project's canvases). One sentence each. */
type FailureKey = SendFailureReason | 'create_failed';

const FAILURE_TEXT: Record<FailureKey, { key: string; fallback: string }> = {
  load_failed: {
    key: 'assets.sendToCanvas.err.loadFailed',
    fallback: 'Could not open that canvas',
  },
  read_only: {
    key: 'assets.sendToCanvas.err.readOnly',
    fallback: 'You can only read that canvas',
  },
  conflict: {
    key: 'assets.sendToCanvas.err.conflict',
    fallback: 'Someone else is editing that canvas. Try again.',
  },
  save_failed: {
    key: 'assets.sendToCanvas.err.saveFailed',
    fallback: 'Could not add the card to that canvas',
  },
  create_failed: {
    key: 'assets.sendToCanvas.err.createFailed',
    fallback: 'Could not create the canvas',
  },
};

export interface SendAssetToCanvasDialogProps {
  detail: AssetRowDetail;
  /** The active loadout, bound on the card so a character arrives wearing
   *  whatever the sheet is currently showing. */
  loadoutId: string | null;
  /** The scope's projects, from the page — same list, same reason, as
   *  `OpenInCanvasLink` (see `SheetSidebarProps.projects`). */
  projects: Project[];
  projectsLoading: boolean;
  projectsFailed: boolean;
  onClose: () => void;
  /** The card landed. The page navigates to `?node=<nodeId>`, which centres it. */
  onDone: (canvasId: string, nodeId: string) => void;
}

export const SendAssetToCanvasDialog: React.FC<SendAssetToCanvasDialogProps> = ({
  detail,
  loadoutId,
  projects,
  projectsLoading,
  projectsFailed,
  onClose,
  onDone,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [project, setProject] = useState<Project | null>(null);
  const [canvases, setCanvases] = useState<Canvas[]>([]);
  const [canvasesLoading, setCanvasesLoading] = useState(false);
  const [canvasesFailed, setCanvasesFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    setCanvasesLoading(true);
    setCanvasesFailed(false);
    listCanvases(String(project.id))
      .then((rows) => {
        if (cancelled) return;
        // 'classic' is the retired 1.0 engine — it has no renderer at all, so
        // a card sent there would be invisible. Every other kind draws the
        // smart node set (storyboard included).
        setCanvases(rows.filter((row) => row.kind !== 'classic'));
        setCanvasesLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[SendAssetToCanvasDialog] listCanvases failed:', err);
        setCanvasesFailed(true);
        setCanvasesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [project]);

  const fail = useCallback(
    (key: FailureKey) => {
      const text = FAILURE_TEXT[key];
      addToast(t(text.key, text.fallback), 'error');
    },
    [addToast, t],
  );

  const send = useCallback(
    async (canvasId: string) => {
      if (busy) return;
      setBusy(true);
      try {
        const result = await sendAssetToCanvas(canvasId, detail, { loadoutId });
        // `=== false`, not `!result.ok` — see `sendAssetToCanvas`: without
        // `strictNullChecks` only an explicit literal comparison narrows a
        // boolean-discriminated union.
        if (result.ok === false) {
          fail(result.reason);
          return;
        }
        onDone(result.canvasId, result.nodeId);
      } finally {
        setBusy(false);
      }
    },
    [busy, detail, loadoutId, fail, onDone],
  );

  const sendToNew = useCallback(async () => {
    if (busy || !project) return;
    setBusy(true);
    let created: Canvas;
    try {
      created = await createCanvas(String(project.id), {
        name: detail.name,
        kind: 'smart',
      });
    } catch (err) {
      console.error('[SendAssetToCanvasDialog] createCanvas failed:', err);
      fail('create_failed');
      setBusy(false);
      return;
    }
    setBusy(false);
    await send(String(created.id));
  }, [busy, project, detail.name, fail, send]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
        data-testid="send-asset-to-canvas-backdrop"
      />
      <div
        role="dialog"
        aria-label={t('assets.sendToCanvas.title', 'Send To Canvas')}
        data-testid="send-asset-to-canvas-dialog"
        className="relative flex max-h-[70vh] w-full max-w-md flex-col rounded-2xl border border-line-strong bg-card shadow-xl"
      >
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            {project && (
              <button
                type="button"
                data-testid="send-asset-back"
                onClick={() => setProject(null)}
                aria-label={t('common.back', 'Back')}
                className="shrink-0 rounded-lg p-1 text-content-3 hover:bg-island-2 hover:text-content"
              >
                <ArrowLeft size={16} />
              </button>
            )}
            <h2 className="truncate text-sm font-semibold text-content">
              {project ? project.name : t('assets.sendToCanvas.title', 'Send To Canvas')}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('common.close', 'Close')}
            className="shrink-0 rounded-lg p-1 text-content-3 hover:bg-island-2 hover:text-content"
          >
            <X size={16} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {!project ? (
            <ProjectStep
              projects={projects}
              loading={projectsLoading}
              failed={projectsFailed}
              onPick={setProject}
            />
          ) : (
            <CanvasStep
              canvases={canvases}
              loading={canvasesLoading}
              failed={canvasesFailed}
              busy={busy}
              onPick={(canvasId) => void send(canvasId)}
              onNew={() => void sendToNew()}
            />
          )}
        </div>
      </div>
    </div>
  );
};

const NOTE = 'px-3 py-6 text-center text-xs text-content-4';
const ROW =
  'flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-content-2 ' +
  'transition-colors hover:bg-island-2 disabled:cursor-not-allowed disabled:opacity-50';

const ProjectStep: React.FC<{
  projects: Project[];
  loading: boolean;
  failed: boolean;
  onPick: (project: Project) => void;
}> = ({ projects, loading, failed, onPick }) => {
  const { t } = useTranslation();
  // Three states, never two — the same rule the sidebar's picker follows.
  if (loading) return <p className={NOTE}>{t('common.loading', 'Loading...')}</p>;
  if (failed) {
    return (
      <p role="alert" className={`${NOTE} text-danger`}>
        {t('assets.projectsUnavailable', 'Projects unavailable')}
      </p>
    );
  }
  if (projects.length === 0) {
    return (
      <p className={NOTE}>{t('assets.sheet.noProjectsToPick', 'Create A Project First')}</p>
    );
  }
  return (
    <ul>
      {projects.map((project) => (
        <li key={String(project.id)}>
          <button
            type="button"
            data-testid="send-asset-project-option"
            data-project-id={String(project.id)}
            onClick={() => onPick(project)}
            className={ROW}
          >
            <span className="min-w-0 flex-1 truncate">{project.name}</span>
          </button>
        </li>
      ))}
    </ul>
  );
};

const CanvasStep: React.FC<{
  canvases: Canvas[];
  loading: boolean;
  failed: boolean;
  busy: boolean;
  onPick: (canvasId: string) => void;
  onNew: () => void;
}> = ({ canvases, loading, failed, busy, onPick, onNew }) => {
  const { t } = useTranslation();
  return (
    <>
      <button
        type="button"
        data-testid="send-asset-new-canvas"
        disabled={busy}
        onClick={onNew}
        className={`${ROW} font-medium text-accent`}
      >
        {busy ? (
          <Loader2 size={14} className="shrink-0 animate-spin" aria-hidden="true" />
        ) : (
          <Plus size={14} className="shrink-0" aria-hidden="true" />
        )}
        {t('assets.sendToCanvas.newCanvas', 'New Canvas')}
      </button>
      {loading ? (
        <p className={NOTE}>{t('common.loading', 'Loading...')}</p>
      ) : failed ? (
        <p role="alert" className={`${NOTE} text-danger`}>
          {t('assets.sendToCanvas.canvasesUnavailable', 'Could not load the canvases here')}
        </p>
      ) : canvases.length === 0 ? (
        <p className={NOTE}>
          {t('assets.sendToCanvas.noCanvases', 'This Project Has No Canvases Yet')}
        </p>
      ) : (
        <ul>
          {canvases.map((canvas) => (
            <li key={String(canvas.id)}>
              <button
                type="button"
                data-testid="send-asset-canvas-option"
                data-canvas-id={String(canvas.id)}
                disabled={busy}
                onClick={() => onPick(String(canvas.id))}
                className={ROW}
              >
                <Layers size={14} className="shrink-0 text-content-4" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">{canvas.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
};

export default SendAssetToCanvasDialog;
