/**
 * CanvasCardMenu — the ··· context menu on a canvas card (IC's card menu:
 * 重命名 / 导出画布 / 删除). Shared by the workspace Canvas module and the
 * canvas landing page so both card surfaces behave identically.
 *
 * Rename fetches the full row first (PUT needs the optimistic-lock token);
 * Export downloads the whole document as a workflow JSON (same payload the
 * composer's selection-Export produces). Cut-to-project and export+assets
 * need backend endpoints — deferred.
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, Loader2, MoreHorizontal, Pencil, Trash2 } from 'lucide-react';

import { getCanvas, saveCanvas } from '../services/canvasService';
import { serializeWorkflow } from '../smart/workflowIO';

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40';

export interface CanvasCardMenuProps {
  canvasId: string;
  canvasName: string;
  onRenamed: (name: string) => void;
  onDelete: () => void;
}

export function CanvasCardMenu({
  canvasId,
  canvasName,
  onRenamed,
  onDelete,
}: CanvasCardMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(canvasName);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false);
        setRenaming(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const commitRename = async () => {
    const next = name.trim();
    if (!next || next === canvasName || busy) {
      setRenaming(false);
      setOpen(false);
      return;
    }
    setBusy(true);
    try {
      const row = await getCanvas(canvasId);
      const result = await saveCanvas(canvasId, {
        base_updated_at: row.base_updated_at,
        name: next,
      });
      if (result.ok) onRenamed(next);
    } catch (err) {
      console.error('[CanvasCardMenu] rename failed:', err);
    } finally {
      setBusy(false);
      setRenaming(false);
      setOpen(false);
    }
  };

  const exportCanvas = async () => {
    setBusy(true);
    try {
      const row = await getCanvas(canvasId);
      const payload = serializeWorkflow(
        row.kind,
        row.nodes_json ?? [],
        row.connections_json ?? [],
      );
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: 'application/json',
      });
      const href = URL.createObjectURL(blob);
      try {
        const a = document.createElement('a');
        a.href = href;
        a.download = `${canvasName || 'canvas'}.json`;
        a.click();
      } finally {
        URL.revokeObjectURL(href);
      }
    } catch (err) {
      console.error('[CanvasCardMenu] export failed:', err);
    } finally {
      setBusy(false);
      setOpen(false);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label={t('canvasList.cardMenu', 'Canvas actions')}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
          setName(canvasName);
        }}
        className={`rounded-lg p-1.5 text-ink-500 transition-colors hover:bg-ink-800 hover:text-ink-200 ${FOCUS_RING}`}
      >
        <MoreHorizontal size={14} />
      </button>
      {open && (
        <div
          role="menu"
          className="mh-pop-in absolute right-0 top-8 z-40 w-44 rounded-xl border border-ink-800 bg-ink-900 p-1 shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          {renaming ? (
            <div className="p-1.5">
              <input
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void commitRename();
                  if (e.key === 'Escape') setRenaming(false);
                }}
                onBlur={() => void commitRename()}
                aria-label={t('canvasList.renameLabel', 'Canvas name')}
                className={`w-full rounded-lg border border-ink-700 bg-ink-950/40 px-2 py-1.5 text-xs text-ink-100 ${FOCUS_RING}`}
              />
            </div>
          ) : (
            <>
              <MenuItem
                icon={busy ? <Loader2 size={12} className="animate-spin" /> : <Pencil size={12} />}
                label={t('canvasList.rename', 'Rename')}
                onClick={() => setRenaming(true)}
              />
              <MenuItem
                icon={<Download size={12} />}
                label={t('canvasList.export', 'Export canvas')}
                onClick={() => void exportCanvas()}
              />
              <div className="mx-1.5 my-1 border-t border-ink-800" />
              <MenuItem
                icon={<Trash2 size={12} />}
                label={t('canvasList.moveToTrash', 'Move to trash')}
                tone="danger"
                onClick={() => {
                  setOpen(false);
                  onDelete();
                }}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  tone?: 'danger';
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors ${
        tone === 'danger'
          ? 'text-rose-400 hover:bg-rose-500/10'
          : 'text-ink-200 hover:bg-ink-800'
      } ${FOCUS_RING}`}
    >
      {icon}
      {label}
    </button>
  );
}

export default CanvasCardMenu;
