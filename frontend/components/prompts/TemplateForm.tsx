//
// The ONE form behind "Save as template", "Save current" and "New" on both
// surfaces. Controlled: the caller owns the value and the submit.
import React from 'react';
import { useTranslation } from 'react-i18next';
import { thumbSrc } from '../../services/promptsService';

export interface TemplateFormValue {
  title: string;
  group: string;
  positive: string;
  negative: string;
  exampleIds: string[];
}

const LABEL = 'block text-[9.5px] font-semibold uppercase tracking-[0.08em] text-content-3 mb-1';
const INPUT = 'w-full rounded-lg border border-line bg-card px-2 py-1.5 text-[12px] text-content outline-none focus:border-accent';

export function TemplateForm({
  value, onChange, examples, groups, disabled,
}: {
  value: TemplateFormValue;
  onChange: (v: TemplateFormValue) => void;
  examples: Array<{ id: string; url: string | null }>;
  groups: string[];
  disabled?: boolean;
}): React.ReactElement {
  const { t } = useTranslation();
  const set = (patch: Partial<TemplateFormValue>) => onChange({ ...value, ...patch });
  const toggle = (id: string) =>
    set({ exampleIds: value.exampleIds.includes(id) ? value.exampleIds.filter((x) => x !== id) : [...value.exampleIds, id] });
  return (
    <div className="flex flex-col gap-3" data-testid="template-form">
      <label className="block">
        <span className={LABEL}>{t('prompts.templateForm.title', 'Title')}</span>
        <input aria-label={t('prompts.templateForm.title', 'Title')} className={INPUT} value={value.title} disabled={disabled} onChange={(e) => set({ title: e.target.value })} />
      </label>
      <div>
        <span className={LABEL}>{t('prompts.templateForm.group', 'Group')}</span>
        <div className="flex flex-wrap items-center gap-1">
          {groups.map((g) => (
            <button key={g} type="button" disabled={disabled} onClick={() => set({ group: value.group === g ? '' : g })}
              className={`rounded-full border px-2 py-0.5 text-[11px] ${value.group === g ? 'border-accent bg-accent-soft text-accent' : 'border-line text-content-3'}`}>
              {g}
            </button>
          ))}
          <input aria-label={t('prompts.templateForm.groupNew', 'Or type a new group')} placeholder={t('prompts.templateForm.groupNew', 'Or type a new group')}
            className={`${INPUT} w-40`} value={groups.includes(value.group) ? '' : value.group} disabled={disabled} onChange={(e) => set({ group: e.target.value })} />
        </div>
      </div>
      {examples.length > 0 && (
        <div>
          <span className={LABEL}>{t('prompts.templateForm.examples', { count: value.exampleIds.length, total: examples.length, defaultValue: 'Examples · {{count}} of {{total}} ticked' })}</span>
          <div className="flex flex-wrap gap-1.5">
            {examples.map((ex, i) => {
              const on = value.exampleIds.includes(ex.id);
              return (
                <button key={ex.id} type="button" data-testid={`template-example-${ex.id}`}
                  aria-label={t('prompts.templateForm.exampleTile', { index: i + 1, defaultValue: 'Example {{index}}' })}
                  aria-pressed={on} disabled={disabled} onClick={() => toggle(ex.id)}
                  className={`h-14 w-14 overflow-hidden rounded-lg border ${on ? 'border-accent ring-2 ring-accent' : 'border-line opacity-50'}`}>
                  {ex.url ? <img src={thumbSrc(ex.url)} alt="" className="h-full w-full object-cover" /> : <span className="block h-full w-full bg-island-2" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
      <label className="block">
        <span className={LABEL}>{t('prompts.templateForm.positive', 'Positive')}</span>
        <textarea aria-label={t('prompts.templateForm.positive', 'Positive')} className={`${INPUT} min-h-[72px] font-mono text-[11.5px]`} value={value.positive} disabled={disabled} onChange={(e) => set({ positive: e.target.value })} />
      </label>
      <label className="block">
        <span className={LABEL}>{t('prompts.templateForm.negative', 'Negative')}</span>
        <textarea aria-label={t('prompts.templateForm.negative', 'Negative')} className={`${INPUT} min-h-[40px] font-mono text-[11.5px]`} value={value.negative} disabled={disabled} onChange={(e) => set({ negative: e.target.value })} />
      </label>
    </div>
  );
}
