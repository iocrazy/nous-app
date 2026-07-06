/**
 * RailModules — the left-rail module navigation (Task 8.5, laper information
 * architecture). Four view slots: Script (the only Phase-1 view, active) and
 * Beats / Storyboard / Scenes as visible-but-disabled placeholders — the same
 * "mode slot" concept as the disabled Outline toolbar items: the slot is shown
 * so the shape of the product is legible, but it can't be selected yet.
 *
 * Icons follow the editor island's monochrome text-glyph convention (as with
 * the ElementToolbar glyphs) rather than importing an icon set here — zero emoji.
 */
import { useTranslation } from 'react-i18next';

interface RailModule {
  key: string;
  labelKey: string;
  glyph: string;
  active?: boolean;
}

const MODULES: RailModule[] = [
  { key: 'script', labelKey: 'editor.moduleScript', glyph: '▤', active: true },
  { key: 'beats', labelKey: 'editor.moduleBeats', glyph: '≡' },
  { key: 'storyboard', labelKey: 'editor.moduleStoryboard', glyph: '▦' },
  { key: 'scenes', labelKey: 'editor.moduleScenes', glyph: '▧' },
];

export function RailModules() {
  const { t } = useTranslation();
  return (
    // A group, not a nav landmark — the rail itself is already the single
    // navigation region; a second landmark would fragment the a11y tree.
    <div className="mh-rail-modules" role="group" aria-label={t('editor.modulesLabel')}>
      {MODULES.map((m) => (
        <button
          type="button"
          key={m.key}
          className={`mh-rail-module${m.active ? ' active' : ''}`}
          aria-current={m.active ? 'page' : undefined}
          disabled={!m.active}
          title={m.active ? undefined : t('editor.comingPhase2')}
        >
          <span className="mh-rail-module-glyph" aria-hidden="true">
            {m.glyph}
          </span>
          {t(m.labelKey)}
        </button>
      ))}
    </div>
  );
}
