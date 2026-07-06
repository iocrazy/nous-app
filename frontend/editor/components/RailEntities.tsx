/**
 * RailEntities — the Characters and Locations sections of the left rail
 * (Task 8.5). Both are read-only navigational lists derived from the loaded
 * scenes: each row shows a name and its scene-appearance count, and clicking it
 * scrolls to the entity's first scene by reusing the shell's scene-scroll
 * callback (the same `onSelect(sceneId)` the SceneRail rows use).
 *
 * Character colour dots share the WritingPanel CAST palette and first-seen
 * order, so a character reads with the same colour in both the rail and the
 * Writing panel.
 */
import { useTranslation } from 'react-i18next';
import type { RailCharacter, RailLocation } from '../railDerive';
import { CAST_COLORS } from './WritingPanel';

export interface RailEntitiesProps {
  characters: RailCharacter[];
  locations: RailLocation[];
  onSelect: (sceneId: string) => void;
}

export function RailEntities({ characters, locations, onSelect }: RailEntitiesProps) {
  const { t } = useTranslation();

  return (
    <>
      <section className="mh-rail-section" aria-label={t('editor.charactersLabel')}>
        <div className="mh-rail-section-label">{t('editor.charactersLabel')}</div>
        {characters.length === 0 ? (
          <div className="mh-rail-empty">{t('editor.charactersEmpty')}</div>
        ) : (
          <div className="mh-rail-entity-list">
            {characters.map((c, i) => (
              <button
                type="button"
                key={c.name}
                className="mh-rail-entity-row"
                onClick={() => onSelect(c.firstSceneId)}
              >
                <span
                  className="mh-cast-dot"
                  style={{ background: CAST_COLORS[i % CAST_COLORS.length] }}
                  aria-hidden="true"
                />
                <span className="mh-rail-entity-name">{c.name}</span>
                <span className="mh-rail-entity-count" title={t('editor.appearsInScenes')}>
                  {c.sceneCount}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="mh-rail-section" aria-label={t('editor.locationsLabel')}>
        <div className="mh-rail-section-label">{t('editor.locationsLabel')}</div>
        {locations.length === 0 ? (
          <div className="mh-rail-empty">{t('editor.locationsEmpty')}</div>
        ) : (
          <div className="mh-rail-entity-list">
            {locations.map((l) => (
              <button
                type="button"
                key={l.name}
                className="mh-rail-entity-row"
                onClick={() => onSelect(l.firstSceneId)}
              >
                <span className="mh-rail-entity-name">{l.name}</span>
                <span className="mh-rail-entity-count" title={t('editor.scenesAtLocation')}>
                  {l.sceneCount}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
