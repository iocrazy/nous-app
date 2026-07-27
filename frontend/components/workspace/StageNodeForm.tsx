/**
 * StageNodeForm — the Stage Board's deliverable-form fill-in surface (M3
 * PR-I §2, task I4). Renders whatever `form_schema` a node's template
 * defined at instantiation (I2/I3's Form tab) as controlled inputs seeded
 * from `form_data`. A field only pushes to the server on BLUR, and only the
 * single changed key rides the wire — `project_stage_nodes_repository`'s
 * write path merges it into the node's existing `form_data` with a
 * per-node key whitelist, so concurrent edits to two different fields (or a
 * stale re-render) never clobber each other.
 *
 * Renders nothing at all when `schema` is empty — WorkspaceStageBoard mounts
 * this unconditionally above the Deliverables section; a pre-mig-390 node
 * (or one whose template never got a Form tab) has `form_schema: []` and
 * this component is a no-op for it (spec §2: "schema 空不渲染").
 *
 * The required badge mirrors the backend's exact fill rule per field type
 * (`formFieldFill.ts` / `advance_service.py::_form_incomplete`) so what the
 * user sees here never disagrees with what the FORM_INCOMPLETE advance gate
 * will actually enforce — but this component is advisory only; the server
 * remains the sole source of truth for whether an advance is blocked (#1400).
 */
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { FormFieldDef, FormFieldType } from '../../types';
import { isFieldFilled } from '../workflow/formFieldFill';

interface StageNodeFormProps {
  schema: FormFieldDef[];
  data: Record<string, unknown>;
  disabled?: boolean;
  /** Fires on blur with ONLY the single field that changed, e.g.
   * `{ summary: 'Q3 recap' }` — never a full-form dump. Caller (WorkspaceStageBoard)
   * PATCHes this straight through as `{ form_data: patch }`. */
  onSave: (patch: Record<string, unknown>) => void;
}

const inputClass =
  'h-8 w-full min-w-0 rounded-md border border-line bg-transparent px-2 text-[13px] text-ink-100 placeholder-ink-600 focus:border-line-strong focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';

export const StageNodeForm: React.FC<StageNodeFormProps> = ({ schema, data, disabled, onSave }) => {
  const { t } = useTranslation();

  if (schema.length === 0) return null;

  return (
    <div data-testid="stage-node-form" className="rounded-xl border border-line bg-island p-4">
      <h3 className="mb-3 text-[11px] uppercase tracking-wider text-ink-500">
        {t('projects.workflow.stageBoard.form')}
      </h3>
      <div className="flex flex-col gap-3">
        {schema.map((field) => (
          <StageNodeFormField
            key={field.key}
            field={field}
            value={data[field.key]}
            disabled={disabled}
            onSave={onSave}
          />
        ))}
      </div>
    </div>
  );
};

const defaultLocal = (type: FormFieldType, value: unknown): unknown => {
  if (value !== undefined && value !== null) return value;
  return type === 'checkbox' ? false : '';
};

const StageNodeFormField: React.FC<{
  field: FormFieldDef;
  value: unknown;
  disabled?: boolean;
  onSave: (patch: Record<string, unknown>) => void;
}> = ({ field, value, disabled, onSave }) => {
  const { t } = useTranslation();
  const [local, setLocal] = useState<unknown>(() => defaultLocal(field.type, value));
  // The last value actually pushed to (or seeded from) the server — commit()
  // only fires onSave when the field genuinely changed, so a blur with no
  // edit (e.g. tabbing through) never manufactures a spurious PATCH.
  const lastSaved = useRef<unknown>(value);

  useEffect(() => {
    setLocal(defaultLocal(field.type, value));
    lastSaved.current = value;
  }, [value, field.key, field.type]);

  const commit = (next: unknown) => {
    if (next === lastSaved.current) return;
    lastSaved.current = next;
    onSave({ [field.key]: next });
  };

  const testId = `stage-form-field-${field.key}`;
  // `isFieldFilled`'s number rule is a KEY-presence check (`key in data`), so
  // the synthetic single-field map below must actually omit the key while
  // the input is blank — passing `{ [field.key]: '' }` would make the key
  // "present" no matter what, permanently reading as filled.
  const liveData = field.type === 'number' && local === '' ? {} : { [field.key]: local };
  const filled = isFieldFilled(field, liveData);

  const label = (
    <label htmlFor={testId} className="flex items-center gap-1.5 text-[12.5px] text-ink-300">
      {field.label}
      {field.required && (
        <span
          data-testid={`stage-form-required-${field.key}`}
          data-filled={String(filled)}
          className={`rounded-full px-1.5 py-0.5 text-[10px] ${
            filled ? 'bg-emerald-500/10 text-emerald-300' : 'bg-amber-500/12 text-amber-400'
          }`}
        >
          {t('projects.workflow.formBuilder.required')}
        </span>
      )}
    </label>
  );

  switch (field.type) {
    case 'textarea':
      return (
        <div className="flex flex-col gap-1">
          {label}
          <textarea
            id={testId}
            data-testid={testId}
            value={(local as string) ?? ''}
            disabled={disabled}
            rows={3}
            onChange={(e) => setLocal(e.target.value)}
            onBlur={() => commit(local)}
            className={`${inputClass} h-auto resize-y py-1.5`}
          />
        </div>
      );

    case 'number':
      return (
        <div className="flex flex-col gap-1">
          {label}
          <input
            id={testId}
            data-testid={testId}
            type="number"
            value={local === '' || local === undefined || local === null ? '' : String(local)}
            disabled={disabled}
            onChange={(e) => setLocal(e.target.value === '' ? '' : Number(e.target.value))}
            onBlur={() => {
              if (local === '') return; // nothing typed — don't manufacture a save.
              commit(Number(local));
            }}
            className={inputClass}
          />
        </div>
      );

    case 'select':
      return (
        <div className="flex flex-col gap-1">
          {label}
          <select
            id={testId}
            data-testid={testId}
            value={(local as string) ?? ''}
            disabled={disabled}
            onChange={(e) => setLocal(e.target.value)}
            onBlur={() => commit(local)}
            className={inputClass}
          >
            <option value="" disabled hidden>
              {t('projects.workflow.stageBoard.selectPlaceholder')}
            </option>
            {(field.options ?? []).map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </div>
      );

    case 'checkbox':
      return (
        <div className="flex items-center gap-2">
          <input
            id={testId}
            data-testid={testId}
            type="checkbox"
            checked={local === true}
            disabled={disabled}
            onChange={(e) => setLocal(e.target.checked)}
            onBlur={() => commit(local)}
            className="h-4 w-4 shrink-0 rounded border-line disabled:cursor-not-allowed disabled:opacity-60"
          />
          {label}
        </div>
      );

    case 'date':
      return (
        <div className="flex flex-col gap-1">
          {label}
          <input
            id={testId}
            data-testid={testId}
            type="date"
            value={(local as string) ?? ''}
            disabled={disabled}
            onChange={(e) => setLocal(e.target.value)}
            onBlur={() => commit(local)}
            className={inputClass}
          />
        </div>
      );

    default:
      // 'text'
      return (
        <div className="flex flex-col gap-1">
          {label}
          <input
            id={testId}
            data-testid={testId}
            type="text"
            value={(local as string) ?? ''}
            disabled={disabled}
            onChange={(e) => setLocal(e.target.value)}
            onBlur={() => commit(local)}
            className={inputClass}
          />
        </div>
      );
  }
};

export default StageNodeForm;
