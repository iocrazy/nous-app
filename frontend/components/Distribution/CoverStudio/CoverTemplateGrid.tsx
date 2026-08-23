// components/Distribution/CoverStudio/CoverTemplateGrid.tsx
//
// The "Templates" card from the Cover Studio design: saved reference pictures,
// most-used first, plus the tile that adds a new one.
//
// Container-agnostic on purpose. Whether Cover Studio ends up as a route or as
// a full-screen layer over the publish page is still open, and that decision
// should not be able to invalidate this component.
//
// `image_url` arrives ready to use in BOTH places it is needed — this grid's
// <img>, and a generation's params.source_urls — because the endpoint it
// points at is unauthenticated by design. The frontend never builds that URL
// and never has to know the rule.

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, X } from 'lucide-react';

import { getApiUrl } from '../../../utils/apiConfig';
import {
  deleteCoverTemplate,
  listCoverTemplates,
  type CoverTemplate,
} from '../../../services/coverTemplateService';
import { AddCoverTemplateModal } from './AddCoverTemplateModal';
import './cover-studio.css';

interface Props {
  scopeId: string;
  /** Templates currently in the 9-image reference pool, by template id. */
  selectedIds?: string[];
  /** Fired when a tile is clicked — the parent owns pool membership. */
  onToggle?: (template: CoverTemplate) => void;
  /** Fired after a template row is deleted, so the pool can drop it too. */
  onRemoved?: (templateId: string) => void;
}

export function CoverTemplateGrid({
  scopeId,
  selectedIds = [],
  onToggle,
  onRemoved,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const [items, setItems] = useState<CoverTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  // Separate from `items.length === 0` — see the .cs-error comment in the CSS:
  // an empty grid says "you have none", which a failed request cannot support.
  const [loadError, setLoadError] = useState(false);
  // A failed DELETE is its own state, not a reuse of loadError. Two reasons:
  // the copy differs (the list loaded fine — the removal is what did not
  // happen), and `load()` clears loadError on entry, so folding them together
  // means the reload that restores the tile also erases the only explanation
  // for why it came back.
  const [removeError, setRemoveError] = useState(false);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      setItems(await listCoverTemplates());
    } catch (err) {
      console.error('[CoverTemplateGrid] listCoverTemplates failed:', err);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = useCallback(
    async (template: CoverTemplate) => {
      // Optimistic: the row is the user's own and the call is a plain delete,
      // so waiting a round-trip to redraw the grid only makes the click feel
      // broken. A failure puts it back AND says so.
      setItems((prev) => prev.filter((i) => i.id !== template.id));
      onRemoved?.(template.id);
      setRemoveError(false);
      try {
        await deleteCoverTemplate(template.id);
      } catch (err) {
        console.error('[CoverTemplateGrid] deleteCoverTemplate failed:', err);
        // Reload FIRST, then flag — `load()` resets loadError on entry, and
        // setting the flag before it would have the reload wipe the message
        // and leave the tile reappearing with no explanation at all.
        await load();
        setRemoveError(true);
      }
    },
    [load, onRemoved],
  );

  const inUse = items.filter((i) => selectedIds.includes(i.id)).length;

  return (
    <div className="cs-card">
      <h4>
        {t('distribution.coverStudio.templates', 'Templates')}
        <span className="aux">
          {loading
            ? t('common.loading', 'Loading…')
            : t('distribution.coverStudio.templateCount', {
                defaultValue: '{{saved}} saved · {{inUse}} in use',
                saved: items.length,
                inUse,
              })}
        </span>
      </h4>

      {loadError && (
        <div className="cs-error">
          {t(
            'distribution.coverStudio.templatesLoadFailed',
            'Could not load your templates.',
          )}
          <button type="button" onClick={() => void load()}>
            {t('common.retry', 'Retry')}
          </button>
        </div>
      )}

      {removeError && (
        <div className="cs-error" data-testid="cover-template-remove-error">
          {t(
            'distribution.coverStudio.templateRemoveFailed',
            'That template could not be removed — it is still saved.',
          )}
        </div>
      )}

      <div className="cs-tpl-grid cs-tpl" data-testid="cover-template-grid">
        {items.map((tpl) => {
          const on = selectedIds.includes(tpl.id);
          return (
            <figure key={tpl.id}>
              <div
                className={`sh ${on ? 'on' : ''}`}
                role="button"
                tabIndex={0}
                aria-pressed={on}
                title={tpl.name}
                onClick={() => onToggle?.(tpl)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onToggle?.(tpl);
                  }
                }}
              >
                <img src={`${getApiUrl()}${tpl.image_url}`} alt={tpl.name} />
                <button
                  type="button"
                  className="rm"
                  aria-label={t(
                    'distribution.coverStudio.removeTemplate',
                    'Remove template',
                  )}
                  onClick={(e) => {
                    // Without this the click also toggles pool membership on
                    // a template that is about to stop existing.
                    e.stopPropagation();
                    void remove(tpl);
                  }}
                >
                  <X size={11} />
                </button>
              </div>
              <figcaption>
                {tpl.name}
                <span>
                  {/* `times`, not `count`: i18next reserves `count` for its
                      plural machinery and would look for usedTimes_one /
                      usedTimes_other, which do not exist. */}
                  {t('distribution.coverStudio.usedTimes', {
                    defaultValue: 'used {{times}}×',
                    times: tpl.usage_count,
                  })}
                </span>
              </figcaption>
            </figure>
          );
        })}

        <figure>
          <button
            type="button"
            className="sh add"
            onClick={() => setAdding(true)}
            data-testid="cover-template-add"
          >
            <Plus size={16} />
            {t('distribution.coverStudio.add', 'Add')}
          </button>
          <figcaption>
            {t('distribution.coverStudio.fromLibrary', 'From library')}
            <span>{t('distribution.coverStudio.orUpload', 'or upload')}</span>
          </figcaption>
        </figure>
      </div>

      {!loading && !loadError && items.length === 0 && (
        <div className="cs-empty">
          {t(
            'distribution.coverStudio.noTemplates',
            'No templates yet. Save a picture you want the model to imitate.',
          )}
        </div>
      )}

      <div className="cs-body">
        <p className="cs-hint">
          {t(
            'distribution.coverStudio.templateExplainer',
            'A template is a picture you liked, kept as an example and handed to the model as a reference image. It does not replace the style, which is text.',
          )}
        </p>
      </div>

      <AddCoverTemplateModal
        open={adding}
        scopeId={scopeId}
        onClose={() => setAdding(false)}
        onAdded={(tpl) => {
          // Re-adding a picture that is already saved is idempotent server-side
          // and returns the EXISTING row, so guard against showing it twice.
          setItems((prev) =>
            prev.some((i) => i.id === tpl.id) ? prev : [...prev, tpl],
          );
        }}
      />
    </div>
  );
}
