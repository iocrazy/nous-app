/**
 * RailModules — the left-rail module navigation (Task 8.5, laper information
 * architecture). View slots: Script (the script sheet) and Scenes (the node
 * projection, Phase B) are selectable; Beats / Storyboard remain
 * visible-but-disabled placeholders — the same "mode slot" concept as the
 * disabled Outline toolbar items: the slot is shown so the shape of the product
 * is legible, but it can't be selected yet.
 *
 * Icons follow the editor island's monochrome text-glyph convention (as with
 * the ElementToolbar glyphs) rather than importing an icon set here — zero emoji.
 */
import { useTranslation } from 'react-i18next';

/** The two central-column views the rail can switch between. */
export type RailView = 'script' | 'nodes';

interface RailModule {
  key: string;
  labelKey: string;
  glyph: string;
  /** The view this slot selects; omitted for not-yet-enabled placeholders. */
  view?: RailView;
}

const MODULES: RailModule[] = [
  { key: 'script', labelKey: 'editor.moduleScript', glyph: '▤', view: 'script' },
  { key: 'beats', labelKey: 'editor.moduleBeats', glyph: '≡' },
  { key: 'storyboard', labelKey: 'editor.moduleStoryboard', glyph: '▦' },
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
