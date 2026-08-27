// components/Distribution/CoverStudio/CoverDrafts.tsx
//
// The "Drafts" card: one 3:4 image holding four cover drafts in a 2x2 grid,
// the pick, and the disclosure showing what was actually sent to the model.
//
// ★ The four drafts are ONE image, not four. That is the skill's Core Rule and
// the reason this stage exists: a round of ideas costs one generation, and only
// the draft the user picks gets redrawn at full size. So the grid here is four
// clickable QUADRANTS of a single <img>, not four separate tiles — there are no
// four images to lay out.

import React from 'react';
import { useTranslation } from 'react-i18next';

import { getApiUrl } from '../../../utils/apiConfig';
import './cover-studio.css';

export type CoverStage = 'idle' | 'drafting' | 'picking' | 'refining' | 'done';

interface Props {
  stage: CoverStage;
  /** The 2x2 grid image, once stage 1 landed. */
  gridUrl?: string;
  /** 1-4, or null before the user picks. */
  selected: number | null;
  onSelect: (n: number) => void;
  onRefine: () => void;
  /** Verbatim from the server — never rebuilt here. */
  prompt?: string;
  /** Set when a generation failed; carries the server's own sentence. */
  error?: string | null;
  /** The grid's shape — 3:4 for the vertical cover, 4:3 for the horizontal. */
  aspect?: '3:4' | '4:3';
}

const QUADRANTS = [1, 2, 3, 4] as const;

export function CoverDrafts({
  stage,
  gridUrl,
  selected,
  onSelect,
  onRefine,
  prompt,
  error,
  aspect = '3:4',
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const busy = stage === 'drafting' || stage === 'refining';

  const step = (n: 1 | 2 | 3, labelKey: string, fallback: string) => {
    const reached =
      (n === 1 && (gridUrl || busy)) ||
      (n === 2 && gridUrl) ||
      (n === 3 && stage === 'done');
    const now =
      (n === 1 && stage === 'drafting') ||
      (n === 2 && stage === 'picking') ||
      (n === 3 && stage === 'refining');
    const done = (n === 1 && !!gridUrl) || (n === 2 && selected !== null && stage !== 'picking');
    return (
      <span className={`cs-step ${done ? 'done' : ''} ${now ? 'now' : ''}`}>
        <span className="num">{done ? '✓' : n}</span>
        {t(labelKey, fallback)}
      </span>
    );
  };

  return (
    <div className="cs-card">
      <h4>{t('distribution.coverStudio.drafts', 'Drafts')}</h4>

      <div className="cs-steps">
        {step(1, 'distribution.coverStudio.stepDrafts', 'Four drafts')}
        <span className="bar" />
        {step(2, 'distribution.coverStudio.stepPick', 'Pick one')}
        <span className="bar" />
        {step(3, 'distribution.coverStudio.stepFinal', 'Final cover')}
      </div>

      {error && (
        <div className="cs-error" data-testid="cover-drafts-error">
          {error}
        </div>
      )}

      <div className="cs-gridwrap">
        <div className={`cs-draftgrid ${aspect === '4:3' ? 'h' : ''}`} data-testid="cover-draft-grid" data-aspect={aspect}>
          {gridUrl ? (
            <>
              {/* One image, four hit areas laid over it. Slicing the picture
                  into four <img> crops would misrepresent what the model
                  produced and cost four times the requests to render. */}
              <img src={`${getApiUrl()}${gridUrl}`} alt={t('distribution.coverStudio.drafts', 'Drafts')} />
              {QUADRANTS.map((n) => (
                <button
                  key={n}
                  type="button"
                  className={`cell q${n} ${selected === n ? 'on' : ''}`}
                  aria-pressed={selected === n}
                  aria-label={t('distribution.coverStudio.draftN', {
                    defaultValue: 'Draft {{n}}',
                    n,
                  })}
                  onClick={() => onSelect(n)}
                  data-testid={`cover-draft-${n}`}
                >
                  <span className="no">{n}</span>
                </button>
              ))}
            </>
          ) : (
            <div className="cs-draft-empty" data-testid="cover-draft-empty">
              {busy
                ? t('distribution.coverStudio.drafting', 'Drawing four drafts…')
                : t(
                    'distribution.coverStudio.draftsEmpty',
                    'Four drafts will appear here as one picture.',
                  )}
            </div>
          )}
        </div>

        <div className="cs-pickside">
          <div className="h">
            {t('distribution.coverStudio.oneImageFour', 'One image, four directions')}
          </div>
          <p className="cs-hint">
            {t(
              'distribution.coverStudio.oneImageFourWhy',
              'The model draws all four in a single picture, so a round of ideas costs one generation instead of four.',
            )}
          </p>

          <div className="cs-picks">
            {QUADRANTS.map((n) => (
              <button
                key={n}
                type="button"
                className={`pk ${selected === n ? 'on' : ''}`}
                disabled={!gridUrl}
                aria-pressed={selected === n}
                onClick={() => onSelect(n)}
              >
                {n}
              </button>
            ))}
          </div>

          <button
            type="button"
            className="cs-primary"
            disabled={!gridUrl || selected === null || busy}
            onClick={onRefine}
            data-testid="cover-make-final"
          >
            {stage === 'refining'
              ? t('distribution.coverStudio.refining', 'Redrawing…')
              : t('distribution.coverStudio.makeFinal', {
                  defaultValue: 'Make #{{n}} the final cover',
                  n: selected ?? 1,
                })}
          </button>
          <p className="cs-hint" style={{ marginTop: 7 }}>
            {t(
              'distribution.coverStudio.refineNote',
              'A second pass redraws it full size and drops the number.',
            )}
          </p>
        </div>
      </div>

      {/* Verbatim, and only what the server actually sent. The panel exists so
          the user can see why a cover came out the way it did; a
          reconstructed-looking prompt would answer that question wrongly. */}
      {prompt && (
        <details className="cs-prompt">
          <summary>
            {t('distribution.coverStudio.whatWasSent', 'What was sent to the model')}
          </summary>
          <div className="cs-promptbox">
            <pre data-testid="cover-prompt-text">{prompt}</pre>
          </div>
        </details>
      )}
    </div>
  );
}
