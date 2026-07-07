/**
 * RailModules — the left-rail module navigation (Task 8.5, laper information
 * architecture). View slots: Script (the script sheet), Beats (the beat sheet),
 * Scenes (the node projection) and Storyboard (the shot board, Phase B P3) are
 * all selectable. A slot with no `view` renders visible-but-disabled — the same
 * "mode slot" concept that keeps the shape of the product legible before a slot
 * is wired up.
 *
 * Icons follow the editor island's monochrome text-glyph convention (as with
 * the ElementToolbar glyphs) rather than importing an icon set here — zero emoji.
 */
import { useTranslation } from 'react-i18next';

/** The central-column views the rail can switch between. */
export type RailView = 'script' | 'nodes' | 'storyboard' | 'beats';

interface RailModule {
  key: string;
  labelKey: string;
  glyph: string;
  /** The view this slot selects; omitted for not-yet-enabled placeholders. */
  view?: RailView;
}

const MODULES: RailModule[] = [
  { key: 'script', labelKey: 'editor.moduleScript', glyph: '▤', view: 'script' },
  { key: 'beats', labelKey: 'editor.moduleBeats', glyph: '≡', view: 'beats' },
  { key: 'storyboard', labelKey: 'editor.moduleStoryboard', glyph: '▦', view: 'storyboard' },
  { key: 'scenes', labelKey: 'editor.moduleScenes', glyph: '▧', view: 'nodes' },
];

export function RailModules({
  activeView,
  onSelect,
}: {
  activeView: RailView;
  onSelect: (view: RailView) => void;
}) {
  const { t } = useTranslation();
  return (
    // A group, not a nav landmark — the rail itself is already the single
    // navigation region; a second landmark would fragment the a11y tree.
    <div className="mh-rail-modules" role="group" aria-label={t('editor.modulesLabel')}>
      {MODULES.map((m) => {
        const selectable = m.view !== undefined;
        const isActive = selectable && m.view === activeView;
        return (
          <button
            type="button"
            key={m.key}
            className={`mh-rail-module${isActive ? ' active' : ''}`}
            aria-current={isActive ? 'page' : undefined}
            disabled={!selectable}
            title={selectable ? undefined : t('editor.comingPhase2')}
            onClick={selectable ? () => onSelect(m.view as RailView) : undefined}
          >
            <span className="mh-rail-module-glyph" aria-hidden="true">
              {m.glyph}
            </span>
            {t(m.labelKey)}
          </button>
        );
      })}
    </div>
  );
}
