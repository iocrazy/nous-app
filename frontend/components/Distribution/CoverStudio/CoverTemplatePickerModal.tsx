// components/Distribution/CoverStudio/CoverTemplatePickerModal.tsx
//
// The template library as a PICKER, not a wall. The folder is meant to hold
// hundreds of pictures, so the studio never lists them inline: this dialog
// searches by name, loads a page at a time, lets the user tick several, and
// hands them back as references. "Add to library" (upload / from the
// library) lives here too, so a picture that is not a template yet can become
// one without leaving the picker.

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Search } from 'lucide-react';

import { UiButton, UiModal } from '../../ui';
import { getApiUrl } from '../../../utils/apiConfig';
import {
  listCoverTemplates,
  type CoverTemplate,
  type CoverTemplateFolder,
} from '../../../services/coverTemplateService';
import { AddCoverTemplateModal } from './AddCoverTemplateModal';
import './cover-studio.css';

const PAGE = 48;

interface Props {
  open: boolean;
  scopeId: string;
  /** Resource ids already in the pool — shown ticked and not re-added. */
  alreadyIn?: string[];
  /** How many more the pool can take; the picker refuses to over-select. */
  room: number;
  onClose: () => void;
  onPick: (templates: CoverTemplate[]) => void;
}

export function CoverTemplatePickerModal({
  open,
  scopeId,
  alreadyIn = [],
  room,
  onClose,
  onPick,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const [q, setQ] = useState('');
  const [items, setItems] = useState<CoverTemplate[]>([]);
  const [folder, setFolder] = useState<CoverTemplateFolder | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [picked, setPicked] = useState<CoverTemplate[]>([]);
  const [adding, setAdding] = useState(false);

  const load = useCallback(
    async (offset: number, append: boolean) => {
      setLoading(true);
      setLoadError(false);
      try {
        const page = await listCoverTemplates({ q, limit: PAGE, offset });
        setFolder(page.folder);
        setTotal(page.total);
        setItems((prev) => (append ? [...prev, ...page.items] : page.items));
      } catch (err) {
        console.error('[CoverTemplatePickerModal] list failed:', err);
        setLoadError(true);
      } finally {
        setLoading(false);
      }
    },
    [q],
  );

  // Fresh page on open and on every search change (debounced a touch).
  useEffect(() => {
    if (!open) return undefined;
    const h = setTimeout(() => void load(0, false), q ? 250 : 0);
    return () => clearTimeout(h);
  }, [open, q, load]);

  useEffect(() => {
    if (open) return;
    setPicked([]);
    setQ('');
  }, [open]);

  const toggle = (tpl: CoverTemplate) => {
    if (alreadyIn.includes(tpl.resource_id)) return;
    setPicked((prev) => {
      if (prev.some((p) => p.resource_id === tpl.resource_id)) {
        return prev.filter((p) => p.resource_id !== tpl.resource_id);
      }
      if (prev.length >= room) return prev;
      return [...prev, tpl];
    });
  };

  const hasMore = items.length < total;

  return (
    <UiModal
      isOpen={open}
      title={t('distribution.coverStudio.pickTemplates', 'Pick from the template library')}
      onClose={onClose}
      widthClassName="w-[720px] cs-light-panel"
      containerClassName="cover-studio-modal"
      footer={
        <>
          <span className="cs-picker-count" data-testid="cover-picker-count">
            {t('distribution.coverStudio.pickerCount', {
              defaultValue: '{{n}} picked · room for {{room}}',
              n: picked.length,
              room,
            })}
          </span>
          <UiButton variant="ghost" onClick={onClose}>
            {t('common.cancel', 'Cancel')}
          </UiButton>
          <UiButton
            variant="primary"
            disabled={picked.length === 0}
            onClick={() => {
              onPick(picked);
              onClose();
            }}
            data-testid="cover-picker-confirm"
          >
            {t('distribution.coverStudio.addPicked', {
              defaultValue: 'Add {{n}} as references',
              n: picked.length,
            })}
          </UiButton>
        </>
      }
    >
      <div className="cs-picker-bar">
        <label className="cs-picker-search">
          <Search size={14} />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t('distribution.coverStudio.searchTemplates', 'Search by name…')}
            data-testid="cover-picker-search"
          />
        </label>
        <button type="button" className="cs-ghost" onClick={() => setAdding(true)} data-testid="cover-picker-add">
          <Plus size={13} />
          {t('distribution.coverStudio.addToLibrary', 'Add to library')}
        </button>
        {folder && (
          <span className="cs-hint cs-picker-folder">
            {t('distribution.coverStudio.pickerFolder', {
              defaultValue: '“{{name}}” · {{total}} pictures',
              name: folder.name,
              total,
            })}
          </span>
        )}
      </div>

      {loadError ? (
        <div className="cs-error">
          {t('distribution.coverStudio.templatesLoadFailed', 'Could not load your templates.')}
          <button type="button" onClick={() => void load(0, false)}>
            {t('common.retry', 'Retry')}
          </button>
        </div>
      ) : (
        <div className="cs-tpl-grid cs-tpl cs-picker-grid" data-testid="cover-picker-grid">
          {items.map((tpl) => {
            const inPool = alreadyIn.includes(tpl.resource_id);
            const on = inPool || picked.some((p) => p.resource_id === tpl.resource_id);
            return (
              <figure key={tpl.resource_id}>
                <button
                  type="button"
                  className={`sh ${on ? 'on' : ''} ${inPool ? 'inpool' : ''}`}
                  aria-pressed={on}
                  disabled={inPool}
                  title={inPool ? t('distribution.coverStudio.alreadyInPool', 'Already a reference') : tpl.name}
                  onClick={() => toggle(tpl)}
                  data-testid={`cover-picker-item-${tpl.resource_id}`}
                >
                  <img src={`${getApiUrl()}${tpl.thumb_url}`} alt={tpl.name} />
                </button>
                <figcaption>
                  {tpl.name}
                  <span>
                    {t('distribution.coverStudio.usedTimes', { defaultValue: 'used {{times}}×', times: tpl.usage_count })}
                  </span>
                </figcaption>
              </figure>
            );
          })}
        </div>
      )}

      {!loading && !loadError && items.length === 0 && (
        <div className="cs-empty">
          {q
            ? t('distribution.coverStudio.noTemplateMatch', 'Nothing matches that name.')
            : t('distribution.coverStudio.noTemplates', 'No templates yet. Save a picture you want the model to imitate.')}
        </div>
      )}

      {loading && <div className="cs-empty">{t('common.loading', 'Loading…')}</div>}

      {hasMore && !loading && (
        <div className="cs-picker-more">
          <button type="button" className="cs-ghost" onClick={() => void load(items.length, true)} data-testid="cover-picker-more">
            {t('distribution.coverStudio.loadMore', {
              defaultValue: 'Load more ({{left}} left)',
              left: total - items.length,
            })}
          </button>
        </div>
      )}

      <AddCoverTemplateModal
        open={adding}
        scopeId={scopeId}
        onClose={() => setAdding(false)}
        onAdded={(tpl) => {
          setItems((prev) => (prev.some((i) => i.resource_id === tpl.resource_id) ? prev : [tpl, ...prev]));
          setTotal((n) => n + 1);
          setPicked((prev) => (prev.length < room ? [...prev, tpl] : prev));
        }}
      />
    </UiModal>
  );
}
