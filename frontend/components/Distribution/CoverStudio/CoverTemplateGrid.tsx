// components/Distribution/CoverStudio/CoverTemplateGrid.tsx
//
// The "Templates" card from the Cover Studio design: the pictures in your
// cover-template folder, most-used first, plus the tile that adds a new one.
//
// The library is a folder in the resource library (see coverTemplateService),
// so this grid READS it and ADDS to it; taking a picture out is done where the
// folder lives, in the library — one place to manage it, not two that drift.
//
// Container-agnostic on purpose. Whether Cover Studio ends up as a route or as
// a full-screen layer over the publish page is still open, and that decision
// should not be able to invalidate this component.

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus } from 'lucide-react';

import { getApiUrl } from '../../../utils/apiConfig';
import {
  listCoverTemplates,
  type CoverTemplate,
  type CoverTemplateFolder,
} from '../../../services/coverTemplateService';
import { AddCoverTemplateModal } from './AddCoverTemplateModal';
import './cover-studio.css';

interface Props {
  scopeId: string;
  /** Templates currently in the 9-image reference pool, by resource id. */
  selectedIds?: string[];
  /** Fired when a tile is clicked — the parent owns pool membership. */
  onToggle?: (template: CoverTemplate) => void;
  /** Resource id of the tile whose reference is being resolved, if any. */
  busyId?: string | null;
  /** Render as the bottom half of the merged references card. */
  embedded?: boolean;
}

export function CoverTemplateGrid({
  scopeId,
  selectedIds = [],
  onToggle,
  busyId = null,
  embedded = false,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const [items, setItems] = useState<CoverTemplate[]>([]);
  const [folder, setFolder] = useState<CoverTemplateFolder | null>(null);
  const [loading, setLoading] = useState(true);
  // Separate from `items.length === 0` — see the .cs-error comment in the CSS:
  // an empty grid says "you have none", which a failed request cannot support.
  const [loadError, setLoadError] = useState(false);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const list = await listCoverTemplates();
      setItems(list.items);
      setFolder(list.folder);
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

  const inUse = items.filter((i) => selectedIds.includes(i.resource_id)).length;

  const Heading = embedded ? 'div' : 'h4';
  return (
    <div className={embedded ? 'cs-embedded cs-embedded-tpl' : 'cs-card'}>
      <Heading className={embedded ? 'cs-subhead' : undefined}>
        {embedded
          ? t('distribution.coverStudio.templateLibrary', 'Template library · click to add')
          : t('distribution.coverStudio.templates', 'Templates')}
        <span className="aux">
          {loading
            ? t('common.loading', 'Loading…')
            : t('distribution.coverStudio.templateCount', {
                defaultValue: '{{saved}} saved · {{inUse}} in use',
                saved: items.length,
                inUse,
              })}
        </span>
      </Heading>

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

      <div className="cs-tpl-grid cs-tpl" data-testid="cover-template-grid">
        {items.map((tpl) => {
          const on = selectedIds.includes(tpl.resource_id);
          const busy = busyId === tpl.resource_id;
          return (
            <figure key={tpl.resource_id}>
              <div
                className={`sh ${on ? 'on' : ''} ${busy ? 'busy' : ''}`}
                role="button"
                tabIndex={0}
                aria-pressed={on}
                aria-busy={busy}
                title={tpl.name}
                onClick={() => {
                  if (!busy) onToggle?.(tpl);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    if (!busy) onToggle?.(tpl);
                  }
                }}
              >
                <img src={`${getApiUrl()}${tpl.thumb_url}`} alt={tpl.name} />
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
          {embedded
            ? t(
                'distribution.coverStudio.templateExplainerMerged',
                'The library is the shelf; the slots above are what is sent. A template is a picture you liked, handed to the model as a reference — it does not replace the style.',
              )
            : t(
            'distribution.coverStudio.templateExplainer',
            'A template is a picture you liked, kept as an example and handed to the model as a reference image. It does not replace the style, which is text.',
          )}
        </p>
        {folder && (
          <p className="cs-hint" data-testid="cover-template-folder-note">
            {folder.adopted
              ? t('distribution.coverStudio.templateFolderAdopted', {
                  defaultValue:
                    'These are the pictures in your “{{name}}” folder in the library. It is now a protected system folder — add or remove templates there too.',
                  name: folder.name,
                })
              : t('distribution.coverStudio.templateFolderNote', {
                  defaultValue:
                    'Kept in the “{{name}}” folder in your library — a protected system folder. Add or remove templates there too.',
                  name: folder.name,
                })}
          </p>
        )}
      </div>

      <AddCoverTemplateModal
        open={adding}
        scopeId={scopeId}
        onClose={() => setAdding(false)}
        onAdded={(tpl) => {
          // Linking a picture that is already in the folder is idempotent
          // server-side, so guard against showing it twice.
          setItems((prev) =>
            prev.some((i) => i.resource_id === tpl.resource_id) ? prev : [...prev, tpl],
          );
        }}
      />
    </div>
  );
}
