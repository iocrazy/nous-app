/**
 * BeatsGuideDrawer — a right-side reference drawer (M3) with one tab per
 * methodology. Each tab lists the template's beats in canonical order with the
 * beat name, its timeline window (percentages), and the full guidance blurb —
 * the reading companion to the Apply wizard. Content is entirely i18n; no data
 * writes. Escape / backdrop / × closes it.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { BEAT_TEMPLATES, getTemplate } from './templates';

interface Props {
  initialKey?: string | null;
  onClose: () => void;
}

/** `0–1%` for an interval, `50%` for a point anchor. */
function windowLabel(pctStart: number, pctEnd: number): string {
  return pctEnd <= pctStart ? `${pctStart}%` : `${pctStart}–${pctEnd}%`;
}

export function BeatsGuideDrawer({ initialKey, onClose }: Props) {
  const { t } = useTranslation();
  const [activeKey, setActiveKey] = useState<string>(
    initialKey && getTemplate(initialKey) ? initialKey : BEAT_TEMPLATES[0].key,
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const active = getTemplate(activeKey)!;

  return (
    <div
      className="mh-guide-overlay"
      data-testid="beats-guide-drawer"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <aside className="mh-guide-panel" role="dialog" aria-modal="true" aria-label={t('editor.beatGuideTitle')}>
        <div className="mh-guide-head">
          <span className="mh-guide-title">{t('editor.beatGuideTitle')}</span>
          <button
            type="button"
            className="mh-arr-modal-x"
            data-testid="beats-guide-close"
            aria-label={t('common.cancel')}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className="mh-guide-tabs" role="tablist">
          {BEAT_TEMPLATES.map((tpl) => (
            <button
              key={tpl.key}
              type="button"
              role="tab"
              aria-selected={activeKey === tpl.key}
              className={`mh-guide-tab${activeKey === tpl.key ? ' active' : ''}`}
              data-testid="beats-guide-tab"
              data-key={tpl.key}
              onClick={() => setActiveKey(tpl.key)}
            >
              {t(tpl.nameKey)}
            </button>
          ))}
        </div>

        <div className="mh-guide-body" data-testid="beats-guide-body">
          <p className="mh-guide-desc">{t(active.descKey)}</p>
          <ol className="mh-guide-list">
            {active.beats.map((b) => (
              <li key={b.role} className="mh-guide-item">
                <div className="mh-guide-item-head">
                  <span className="mh-guide-item-name">
                    {t(`editor.beatTpl.${active.key}.${b.role}.name`)}
                  </span>
                  <span className="mh-guide-item-window">{windowLabel(b.pctStart, b.pctEnd)}</span>
                </div>
                <p className="mh-guide-item-guide">
                  {t(`editor.beatTpl.${active.key}.${b.role}.guide`)}
                </p>
              </li>
            ))}
          </ol>
        </div>
      </aside>
    </div>
  );
}
