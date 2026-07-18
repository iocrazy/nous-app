/**
 * SceneToc — the Notion-style table-of-contents for scene navigation.
 *
 * Default state is a minimal column of tick lines (one per scene) floating at
 * the left edge of the paper — it occupies no layout ("又是一个框"的解药: it does
 * NOT push the sheet). The current scene's tick is darker + longer. Hovering the
 * ticks floats a light panel that reuses the existing {@link SceneRail} list
 * (S{n} badge / INT-EXT / title / summary / active highlight / click-to-locate);
 * moving away collapses it back to ticks after a short grace delay. A pin button
 * keeps the panel open, persisted per script (sceneTocStorage).
 *
 * Accessibility: the tick column is a pointer-only visual affordance
 * (aria-hidden), so screen readers navigate the always-present panel list — the
 * SceneRail buttons carry the real semantics and are keyboard-reachable, which
 * also reveals the panel via :focus-within (see editorShellStyles).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pin } from 'lucide-react';
import type { SceneDoc } from '../types';
import { SceneRail } from './SceneRail';
import { persistTocPinned, readStoredTocPinned } from '../sceneTocStorage';

/** Grace window before an un-pinned panel collapses, so a diagonal mouse path
 *  from the ticks to a far row doesn't snap it shut mid-move. */
const CLOSE_DELAY_MS = 300;

export interface SceneTocProps {
  scenes: SceneDoc[];
  activeSceneId: string | null;
  onSelect: (sceneId: string) => void;
  /** Keys the per-script pin memory (sceneTocStorage). */
  scriptId: string;
}

export function SceneToc({ scenes, activeSceneId, onSelect, scriptId }: SceneTocProps) {
  const { t } = useTranslation();
  const [pinned, setPinned] = useState<boolean>(() => readStoredTocPinned(scriptId));
  const [hovered, setHovered] = useState(false);
  const closeTimer = useRef<number | null>(null);

  // Re-read the per-script pin when the script changes (a remount for a new
  // script must not carry the previous script's pin state).
  useEffect(() => {
    setPinned(readStoredTocPinned(scriptId));
    setHovered(false);
  }, [scriptId]);

  const cancelClose = useCallback(() => {
    if (closeTimer.current != null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  useEffect(() => cancelClose, [cancelClose]);

  const handleEnter = useCallback(() => {
    cancelClose();
    setHovered(true);
  }, [cancelClose]);

  const handleLeave = useCallback(() => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => setHovered(false), CLOSE_DELAY_MS);
  }, [cancelClose]);

  const togglePin = useCallback(() => {
    setPinned((prev) => {
      const next = !prev;
      persistTocPinned(scriptId, next);
      return next;
    });
  }, [scriptId]);

  if (scenes.length === 0) return null;

  const open = pinned || hovered;

  return (
    <nav
      className="mh-scene-toc"
      data-open={open ? 'true' : 'false'}
      data-testid="scene-toc"
      aria-label={t('editor.scenesNav')}
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      {/* Minimal tick rail — the always-visible affordance. Pointer-only (the
          panel below is the accessible surface), so aria-hidden. */}
      <div className="mh-toc-ticks" aria-hidden="true">
        {scenes.map((s) => {
          const active = s.id === activeSceneId;
          return (
            <button
              type="button"
              key={s.id}
              className={`mh-toc-tick${active ? ' active' : ''}`}
              data-testid="scene-toc-tick"
              aria-current={active ? 'true' : undefined}
              tabIndex={-1}
              onClick={() => onSelect(s.id)}
            />
          );
        })}
      </div>

      {/* Floating panel — reuses the existing SceneRail list. */}
      <div className="mh-toc-panel" role="group" aria-label={t('editor.scenesLabel')}>
        <div className="mh-toc-panel-head">
          <span className="mh-toc-panel-title">{t('editor.scenesLabel')}</span>
          <span className="mh-toc-panel-count">{scenes.length}</span>
          <button
            type="button"
            className={`mh-icon-btn mh-toc-pin${pinned ? ' pinned' : ''}`}
            data-testid="scene-toc-pin"
            aria-label={pinned ? t('editor.unpinScenes') : t('editor.pinScenes')}
            aria-pressed={pinned}
            onClick={togglePin}
          >
            <Pin size={14} aria-hidden="true" />
          </button>
        </div>
        <div className="mh-toc-panel-body">
          <SceneRail scenes={scenes} activeSceneId={activeSceneId} onSelect={onSelect} />
        </div>
      </div>
    </nav>
  );
}
