/**
 * WritingPanel — the right "Writing" island (spec v3 §3.2 Statistics + Format).
 *
 * Statistics are derived live and purely on the client from the loaded scenes:
 * Scenes, Words (whitespace-split across every element), Characters (distinct
 * character-cue text), Locations (distinct location_text), and a CAST list with
 * colour dots. Page-count is intentionally omitted (depends on the pagination
 * engine, cut for Phase 1 per the plan).
 *
 * Data source note: stats read the shell's loaded `scenes`, not each
 * SceneBlock's in-flight optimistic edits — chosen for simplicity (the shell
 * already holds the loaded list; lifting every block's live elements up would
 * add cross-component plumbing for a read-only sidebar). Counts refresh on the
 * next scene reload; the drift is cosmetic and Phase-1-acceptable.
 */
import { useTranslation } from 'react-i18next';
import type { SceneDoc } from '../types';
import type { EditorFormat } from '../useEditorState';
import type { PaginationMode } from '../paginationStorage';
import { DEFAULT_ZOOM, MAX_ZOOM, MIN_ZOOM, stepZoom } from '../zoomStorage';
import type { ScriptCommit } from '../sceneService';
import { VersionPanel } from '../versions/VersionPanel';

export const CAST_COLORS = [
  'var(--red)',
  'var(--green)',
  'var(--violet)',
  'var(--indigo)',
  'var(--amber)',
];

export interface EditorStatistics {
  scenes: number;
  words: number;
  characters: number;
  locations: number;
  cast: string[];
}

export function deriveStatistics(scenes: SceneDoc[]): EditorStatistics {
  let words = 0;
  const castSet = new Set<string>();
  const cast: string[] = [];
  const locationSet = new Set<string>();

  for (const scene of scenes) {
    const loc = (scene.location_text ?? '').trim();
    if (loc) locationSet.add(loc.toUpperCase());
    for (const el of scene.elements) {
      const trimmed = el.text.trim();
      if (trimmed) words += trimmed.split(/\s+/).length;
      if (el.type === 'character' && trimmed) {
        const key = trimmed.toUpperCase();
        if (!castSet.has(key)) {
          castSet.add(key);
          cast.push(trimmed);
        }
      }
    }
  }

  return {
    scenes: scenes.length,
    words,
    characters: castSet.size,
    locations: locationSet.size,
    cast,
  };
}

export interface WritingPanelProps {
  scenes: SceneDoc[];
  format: EditorFormat;
  onFormatChange: (format: EditorFormat) => void;
  /** Paged (laper-style page rules) vs Continuous flow. Optional so the panel
   *  renders standalone in stats-only tests. */
  pagination?: PaginationMode;
  onPaginationChange?: (mode: PaginationMode) => void;
  /** Display zoom (percent of print size). Optional so the panel renders
   *  standalone in stats-only tests. Purely visual — see zoomStorage.ts. */
  zoom?: number;
  onZoomChange?: (zoom: number) => void;
  // Version history (Phase B P4). Optional so the panel renders standalone
  // (statistics-only) when no script is bound — e.g. the deriveStatistics
  // tests. Compare renders in the margin rail BESIDE the sheet (EditorShell's
  // .mh-diff-rail) so the live text stays visible — only the LIST lives here.
  scriptId?: string;
  onCompareCommit?: (commit: ScriptCommit) => void;
  onRolledBack?: () => void;
}

export function WritingPanel({
  scenes,
  format,
  onFormatChange,
  pagination,
  onPaginationChange,
  zoom,
  onZoomChange,
  scriptId,
  onCompareCommit,
  onRolledBack,
}: WritingPanelProps) {
  const { t } = useTranslation();
  const stats = deriveStatistics(scenes);

  return (
    <div className="mh-panel-body">
      {scriptId && onCompareCommit && onRolledBack && (
        <>
          <VersionPanel
            scriptId={scriptId}
            onCompare={onCompareCommit}
            onRolledBack={onRolledBack}
          />
          <div className="mh-divider" />
        </>
      )}

      {pagination && onPaginationChange && (
        <>
          <div>
            <div className="mh-field-label">{t('editor.pagination')}</div>
            <div className="mh-segmented" role="group" aria-label={t('editor.pagination')}>
              <button
                type="button"
                className={`mh-seg${pagination === 'continuous' ? ' active' : ''}`}
                aria-pressed={pagination === 'continuous'}
                onClick={() => onPaginationChange('continuous')}
              >
                {t('editor.continuous')}
              </button>
              <button
                type="button"
                className={`mh-seg${pagination === 'paged' ? ' active' : ''}`}
                aria-pressed={pagination === 'paged'}
                onClick={() => onPaginationChange('paged')}
              >
                {t('editor.paged')}
              </button>
            </div>
          </div>
          <div className="mh-divider" />
        </>
      )}

      {zoom != null && onZoomChange && (
        <>
          <div>
            <div className="mh-field-label">{t('editor.zoom')}</div>
            <div className="mh-zoom" role="group" aria-label={t('editor.zoom')}>
              <button
                type="button"
                className="mh-zoom-btn"
                aria-label={t('editor.zoomOut')}
                data-testid="zoom-out"
                disabled={zoom <= MIN_ZOOM}
                onClick={() => onZoomChange(stepZoom(zoom, -1))}
              >
                −
              </button>
              <button
                type="button"
                className="mh-zoom-value"
                aria-label={t('editor.zoomReset')}
                title={t('editor.zoomReset')}
                onClick={() => onZoomChange(DEFAULT_ZOOM)}
                data-testid="zoom-value"
              >
                {zoom}%
              </button>
              <button
                type="button"
                className="mh-zoom-btn"
                aria-label={t('editor.zoomIn')}
                data-testid="zoom-in"
                disabled={zoom >= MAX_ZOOM}
                onClick={() => onZoomChange(stepZoom(zoom, 1))}
              >
                +
              </button>
            </div>
          </div>
          <div className="mh-divider" />
        </>
      )}

      <div>
        <div className="mh-field-label">{t('editor.format')}</div>
        <div className="mh-segmented" role="group" aria-label={t('editor.format')}>
          <button
            type="button"
            className={`mh-seg${format === 'hollywood' ? ' active' : ''}`}
            aria-pressed={format === 'hollywood'}
            onClick={() => onFormatChange('hollywood')}
          >
            {t('editor.hollywood')}
          </button>
          <button
            type="button"
            className={`mh-seg${format === 'asian' ? ' active' : ''}`}
            aria-pressed={format === 'asian'}
            onClick={() => onFormatChange('asian')}
          >
            {t('editor.asian')}
          </button>
        </div>
      </div>

      <div className="mh-divider" />

      <div>
        <div className="mh-field-label">{t('editor.statistics')}</div>
        <div className="mh-stats-grid">
          <div className="mh-stat-tile">
            <div className="mh-stat-num" data-testid="stat-scenes">
              {stats.scenes}
            </div>
            <div className="mh-stat-lbl">{t('editor.statScenes')}</div>
          </div>
          <div className="mh-stat-tile v2">
            <div className="mh-stat-num" data-testid="stat-words">
              {stats.words}
            </div>
            <div className="mh-stat-lbl">{t('editor.statWords')}</div>
          </div>
          <div className="mh-stat-tile v2">
            <div className="mh-stat-num" data-testid="stat-characters">
              {stats.characters}
            </div>
            <div className="mh-stat-lbl">{t('editor.statCharacters')}</div>
          </div>
          <div className="mh-stat-tile">
            <div className="mh-stat-num" data-testid="stat-locations">
              {stats.locations}
            </div>
            <div className="mh-stat-lbl">{t('editor.statLocations')}</div>
          </div>
        </div>
      </div>

      <div className="mh-divider" />

      <div>
        <div className="mh-field-label">{t('editor.cast')}</div>
        {stats.cast.length === 0 ? (
          <div className="mh-panel-hint">{t('editor.castEmpty')}</div>
        ) : (
          stats.cast.map((name, i) => (
            <div className="mh-cast-row" key={name}>
              <span
                className="mh-cast-dot"
                style={{ background: CAST_COLORS[i % CAST_COLORS.length] }}
                aria-hidden="true"
              />
              {name}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
