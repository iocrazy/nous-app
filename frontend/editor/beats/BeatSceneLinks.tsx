/**
 * BeatSceneLinks — the linked-scene chip row + "link scene" picker for a beat.
 *
 * Extracted from BeatCard (M2) so the List card and the Arrangement Edit-Beat
 * modal share one implementation. Markup + testids (`beat-scene-chip`,
 * `beat-link-scene`) are unchanged from the original BeatCard block so the M1
 * list regressions keep passing. `onChange` receives the full next id list;
 * `onOpenScene` (optional) jumps to a linked scene.
 */
import { useTranslation } from 'react-i18next';

import type { SceneDoc } from '../types';
import { UiSelect } from '../../components/ui';

/** `INT · Location · TIME`, empty parts dropped (mirrors OutlineView's head). */
export function sceneLabel(scene: SceneDoc, fallback: string): string {
  const label = [scene.heading_int_ext, scene.location_text, scene.time_of_day]
    .map((part) => (part ?? '').trim())
    .filter((part) => part.length > 0)
    .join(' · ');
  return label || fallback;
}

interface Props {
  linkedIds: string[];
  scenes: SceneDoc[];
  onChange: (nextIds: string[]) => void;
  onOpenScene?: (sceneId: string) => void;
}

export function BeatSceneLinks({ linkedIds, scenes, onChange, onOpenScene }: Props) {
  const { t } = useTranslation();
  const unlinkedScenes = scenes.filter((s) => !linkedIds.includes(String(s.id)));

  return (
    <div className="mh-beat-scenes">
      {linkedIds.map((sceneId) => {
        const scene = scenes.find((s) => String(s.id) === String(sceneId));
        const label = scene
          ? sceneLabel(scene, t('editor.untitledScene'))
          : t('editor.beatSceneMissing');
        return (
          <span className="mh-beat-chip" key={sceneId} data-testid="beat-scene-chip">
            <button
              type="button"
              className="mh-beat-chip-label"
              disabled={!scene}
              onClick={() => scene && onOpenScene?.(String(sceneId))}
            >
              {label}
            </button>
            <button
              type="button"
              className="mh-beat-chip-x"
              aria-label={t('editor.beatUnlinkScene')}
              onClick={() => onChange(linkedIds.filter((id) => id !== sceneId))}
            >
              ×
            </button>
          </span>
        );
      })}
      {unlinkedScenes.length > 0 && (
        <UiSelect
          triggerClassName="mh-beat-link-select"
          data-testid="beat-link-scene"
          aria-label={t('editor.beatLinkScene')}
          value=""
          onChange={(e) => {
            const id = e.target.value;
            if (id) onChange([...linkedIds, id]);
          }}
        >
          <option value="">{t('editor.beatLinkScene')}</option>
          {unlinkedScenes.map((s) => (
            <option key={s.id} value={String(s.id)}>
              {sceneLabel(s, t('editor.untitledScene'))}
            </option>
          ))}
        </UiSelect>
      )}
    </div>
  );
}
