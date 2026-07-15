/**
 * ArrangeSelectedButton — IC's floating 整理选中 affordance (minimap-arrange-btn).
 * Sits just above the bottom-right minimap and fades in when 2+ top-level
 * nodes are selected; one click lays that subset out in place (shared
 * arrangeSelected logic). Smart-family canvases only — mounted by CanvasPage.
 */

import { useCallback } from 'react';
import { LayoutGrid } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { arrangeSelected, canArrangeSelection } from '../smart/arrangeNodes';

export function ArrangeSelectedButton() {
  const { t } = useTranslation();
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const selection = useCanvasCoreStore((s) => s.selection);

  const visible = canArrangeSelection(nodes, selection);

  const onArrange = useCallback(() => {
    const store = useCanvasCoreStore.getState();
    const next = arrangeSelected(store.nodes, store.connections, store.selection);
    if (next) store.setNodes(next);
  }, []);

  if (!visible) return null;

  return (
    <button
      type="button"
      data-testid="arrange-selected-btn"
      onClick={onArrange}
      className="canvas-island pointer-events-auto absolute bottom-[9.5rem] right-4 z-30 flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-bold text-canvas-muted transition-colors hover:text-canvas-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
    >
      <LayoutGrid size={14} />
      {t('canvas.arrangeSelected', 'Arrange selected')}
    </button>
  );
}

export default ArrangeSelectedButton;
