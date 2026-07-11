/**
 * SceneRail — the left navigation list of scenes (spec v3 §3.1).
 *
 * Each row shows the scene number, an INT/EXT badge, the location, and a
 * truncated first-action summary. Clicking a row calls `onSelect(sceneId)`;
 * the shell scrolls the matching SceneBlock into view and marks it active.
 * Viewport auto-highlight (IntersectionObserver over the SceneBlocks) is wired
 * in the shell with a feature-detect, since jsdom has no IO.
 */
import { useTranslation } from 'react-i18next';
import type { SceneDoc } from '../types';

const SUMMARY_MAX = 40;

function firstActionSummary(scene: SceneDoc): string {
  const action = scene.elements.find((el) => el.type === 'action' && el.text.trim() !== '');
  const text = action?.text.trim() ?? '';
  return text.length > SUMMARY_MAX ? `${text.slice(0, SUMMARY_MAX).trimEnd()}…` : text;
}

export interface SceneRailProps {
  scenes: SceneDoc[];
  activeSceneId: string | null;
  onSelect: (sceneId: string) => void;
}

export function SceneRail({ scenes, activeSceneId, onSelect }: SceneRailProps) {
  const { t } = useTranslation();

  return (
    <div className="mh-scene-list" data-testid="scene-rail">
      {scenes.map((s, i) => {
        const ext = (s.heading_int_ext ?? '').toUpperCase().includes('EXT');
        const summary = firstActionSummary(s);
        return (
          <button
            type="button"
            key={s.id}
            className={`mh-scene-row${activeSceneId === s.id ? ' active' : ''}`}
            aria-current={activeSceneId === s.id ? 'true' : undefined}
            onClick={() => onSelect(s.id)}
          >
            <span className="mh-scene-num-chip">S{i + 1}</span>
            <span className="mh-scene-meta-text">
              <span className="mh-scene-row-head">
                <span className={`mh-ie-badge ${ext ? 'ext' : 'int'}`}>{ext ? 'EXT' : 'INT'}</span>
                <span className="mh-scene-title">
                  {s.location_text || t('editor.untitledScene')}
                </span>
              </span>
              <span className="mh-scene-slug">{summary || t('editor.emptyScene')}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
