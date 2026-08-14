/**
 * StageNodeForm (M3 PR-I §2, task I4) — the Stage Board's deliverable-form
 * fill-in surface. Pins: all six control types render from `schema`; a
 * field's onBlur fires `onSave` with ONLY that field's `{ key: value }` —
 * never a full-form dump, since the server-side merge-with-whitelist in
 * `project_stage_nodes_repository` only needs the changed key; and the
 * required badge mirrors the backend's exact fill rule
 * (`advance_service.py::_form_incomplete`): text/textarea/select/date empty
 * string = unfilled, number missing key = unfilled (0 counts filled),
 * checkbox anything but `true` = unfilled.
 */
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { StageNodeForm } from './StageNodeForm';
import type { FormFieldDef } from '../../types';

function makeI18n(): I18n {
  const instance = createInstance();
  instance.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
  });
  return instance;
}

function renderForm(
  schema: FormFieldDef[],
  data: Record<string, unknown> = {},
  opts: { disabled?: boolean; onSave?: (patch: Record<string, unknown>) => void } = {},
) {
  const onSave = opts.onSave ?? vi.fn();
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <StageNodeForm schema={schema} data={data} disabled={opts.disabled} onSave={onSave} />
    </I18nextProvider>,
  );
  return { ...utils, onSave };
}

afterEach(() => cleanup());

const ALL_TYPES_SCHEMA: FormFieldDef[] = [
  { key: 'summary', label: 'Summary', type: 'text', required: false },
  { key: 'notes', label: 'Notes', type: 'textarea', required: false },
  { key: 'budget', label: 'Budget', type: 'number', required: false },
  { key: 'category', label: 'Category', type: 'select', required: false, options: ['A', 'B'] },
  { key: 'approved', label: 'Approved', type: 'checkbox', required: false },
  { key: 'due', label: 'Due', type: 'date', required: false },
];

describe('StageNodeForm — rendering', () => {
  it('renders nothing when schema is empty', () => {
    const { container } = renderForm([]);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders all six control types from schema', () => {
    renderForm(ALL_TYPES_SCHEMA);
    expect(screen.getByTestId('stage-form-field-summary').tagName).toBe('INPUT');
    expect(screen.getByTestId('stage-form-field-summary')).toHaveAttribute('type', 'text');
    expect(screen.getByTestId('stage-form-field-notes').tagName).toBe('TEXTAREA');
    expect(screen.getByTestId('stage-form-field-budget')).toHaveAttribute('type', 'number');
    expect(screen.getByTestId('stage-form-field-category').tagName).toBe('SELECT');
    expect(screen.getByTestId('stage-form-field-approved')).toHaveAttribute('type', 'checkbox');
    // `date` is a trigger button for the shared DateTimePopover, not a native
    // `<input type="date">` — the testid is unchanged, the element is not.
    expect(screen.getByTestId('stage-form-field-due').tagName).toBe('BUTTON');
  });

  it('seeds each control from `data`', () => {
    renderForm(ALL_TYPES_SCHEMA, {
      summary: 'Hello',
      notes: 'Some notes',
      budget: 42,
      category: 'B',
      approved: true,
      due: '2026-08-01',
    });
    expect(screen.getByTestId('stage-form-field-summary')).toHaveValue('Hello');
    expect(screen.getByTestId('stage-form-field-notes')).toHaveValue('Some notes');
    expect(screen.getByTestId('stage-form-field-budget')).toHaveValue(42);
    expect(screen.getByTestId('stage-form-field-category')).toHaveValue('B');
    expect(screen.getByTestId('stage-form-field-approved')).toBeChecked();
    // Button, so the seeded value reads off the label rather than `value`.
    expect(screen.getByTestId('stage-form-field-due').textContent).toBe('2026-08-01');
  });

  it('disabled prop disables every control', () => {
    renderForm(ALL_TYPES_SCHEMA, {}, { disabled: true });
    expect(screen.getByTestId('stage-form-field-summary')).toBeDisabled();
    expect(screen.getByTestId('stage-form-field-notes')).toBeDisabled();
    expect(screen.getByTestId('stage-form-field-budget')).toBeDisabled();
    expect(screen.getByTestId('stage-form-field-category')).toBeDisabled();
    expect(screen.getByTestId('stage-form-field-approved')).toBeDisabled();
    expect(screen.getByTestId('stage-form-field-due')).toBeDisabled();
  });
});

describe('StageNodeForm — blur-save fires onSave with only the changed key', () => {
  it('text field', () => {
    const onSave = vi.fn();
    renderForm([{ key: 'summary', label: 'Summary', type: 'text', required: false }], {}, { onSave });
    const field = screen.getByTestId('stage-form-field-summary');
    fireEvent.change(field, { target: { value: 'Q3 recap' } });
    fireEvent.blur(field);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith({ summary: 'Q3 recap' });
  });

  it('textarea field', () => {
    const onSave = vi.fn();
    renderForm(
      [
        { key: 'summary', label: 'Summary', type: 'text', required: false },
        { key: 'notes', label: 'Notes', type: 'textarea', required: false },
      ],
      { summary: 'unchanged' },
      { onSave },
    );
    const field = screen.getByTestId('stage-form-field-notes');
    fireEvent.change(field, { target: { value: 'Details here' } });
    fireEvent.blur(field);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith({ notes: 'Details here' });
  });

  it('number field — 0 is a real save, never skipped as falsy', () => {
    const onSave = vi.fn();
    renderForm([{ key: 'budget', label: 'Budget', type: 'number', required: false }], {}, { onSave });
    const field = screen.getByTestId('stage-form-field-budget');
    fireEvent.change(field, { target: { value: '0' } });
    fireEvent.blur(field);
    expect(onSave).toHaveBeenCalledWith({ budget: 0 });
  });

  it('select field', () => {
    const onSave = vi.fn();
    renderForm(
      [{ key: 'category', label: 'Category', type: 'select', required: false, options: ['A', 'B'] }],
      {},
      { onSave },
    );
    const field = screen.getByTestId('stage-form-field-category');
    fireEvent.change(field, { target: { value: 'B' } });
    fireEvent.blur(field);
    expect(onSave).toHaveBeenCalledWith({ category: 'B' });
  });

  it('checkbox field', () => {
    const onSave = vi.fn();
    renderForm([{ key: 'approved', label: 'Approved', type: 'checkbox', required: false }], {}, { onSave });
    const field = screen.getByTestId('stage-form-field-approved');
    fireEvent.click(field);
    fireEvent.blur(field);
    expect(onSave).toHaveBeenCalledWith({ approved: true });
  });

  // The popover has no blur to commit on, so the pick IS the commit — same
  // single-key patch, same string value.
  it('date field', () => {
    const onSave = vi.fn();
    // Seeded so the calendar opens on the month under test rather than on
    // whatever month the machine clock happens to be in.
    renderForm(
      [{ key: 'due', label: 'Due', type: 'date', required: false }],
      { due: '2026-08-15' },
      { onSave },
    );
    fireEvent.click(screen.getByTestId('stage-form-field-due'));
    fireEvent.click(screen.getByLabelText('2026-08-01'));
    expect(onSave).toHaveBeenCalledWith({ due: '2026-08-01' });
  });

  it('blurring without a change never calls onSave', () => {
    const onSave = vi.fn();
    renderForm(
      [{ key: 'summary', label: 'Summary', type: 'text', required: false }],
      { summary: 'Already set' },
      { onSave },
    );
    fireEvent.blur(screen.getByTestId('stage-form-field-summary'));
    expect(onSave).not.toHaveBeenCalled();
  });

  it('editing one field never includes any other field key in the same onSave call', () => {
    const onSave = vi.fn();
    renderForm(ALL_TYPES_SCHEMA, { summary: 'existing', budget: 10 }, { onSave });
    const field = screen.getByTestId('stage-form-field-summary');
    fireEvent.change(field, { target: { value: 'changed' } });
    fireEvent.blur(field);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toEqual({ summary: 'changed' });
  });

  it('number field: blurring an emptied input does not manufacture a save', () => {
    const onSave = vi.fn();
    renderForm([{ key: 'budget', label: 'Budget', type: 'number', required: false }], { budget: 5 }, { onSave });
    const field = screen.getByTestId('stage-form-field-budget');
    fireEvent.change(field, { target: { value: '' } });
    fireEvent.blur(field);
    expect(onSave).not.toHaveBeenCalled();
  });
});

describe('StageNodeForm — fresh node (key absent from form_data): focus+blur without editing never fires a phantom save', () => {
  // Regression (code review): `lastSaved.current` used to be seeded from the
  // RAW `value` prop (`undefined` when the key is absent, as on every brand
  // new node) while `commit()` compares against the DEFAULTED `local` state
  // (`''`/`false`) — so `'' !== undefined` on the very first blur, even with
  // no edit, fired a phantom `onSave({ key: '' })` / `onSave({ key: false })`.
  // number was accidentally immune (its own `local === ''` guard skips the
  // commit call entirely), but text/textarea/select/checkbox/date were not.
  it('text field', () => {
    const onSave = vi.fn();
    renderForm([{ key: 'summary', label: 'Summary', type: 'text', required: false }], {}, { onSave });
    const field = screen.getByTestId('stage-form-field-summary');
    fireEvent.focus(field);
    fireEvent.blur(field);
    expect(onSave).not.toHaveBeenCalled();
  });

  it('textarea field', () => {
    const onSave = vi.fn();
    renderForm([{ key: 'notes', label: 'Notes', type: 'textarea', required: false }], {}, { onSave });
    const field = screen.getByTestId('stage-form-field-notes');
    fireEvent.focus(field);
    fireEvent.blur(field);
    expect(onSave).not.toHaveBeenCalled();
  });

  it('select field', () => {
    const onSave = vi.fn();
    renderForm(
      [{ key: 'category', label: 'Category', type: 'select', required: false, options: ['A', 'B'] }],
      {},
      { onSave },
    );
    const field = screen.getByTestId('stage-form-field-category');
    fireEvent.focus(field);
    fireEvent.blur(field);
    expect(onSave).not.toHaveBeenCalled();
  });

  it('checkbox field', () => {
    const onSave = vi.fn();
    renderForm([{ key: 'approved', label: 'Approved', type: 'checkbox', required: false }], {}, { onSave });
    const field = screen.getByTestId('stage-form-field-approved');
    fireEvent.focus(field);
    fireEvent.blur(field);
    expect(onSave).not.toHaveBeenCalled();
  });

  // The popover's equivalent of "blurred without editing": opened, then
  // dismissed without picking anything.
  it('date field', () => {
    const onSave = vi.fn();
    renderForm(
      [{ key: 'due', label: 'Due', type: 'date', required: false }],
      { due: '2026-08-15' },
      { onSave },
    );
    fireEvent.click(screen.getByTestId('stage-form-field-due'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onSave).not.toHaveBeenCalled();
  });

  it('positive control: editing then blurring still fires exactly once (the fix must not over-suppress real edits)', () => {
    const onSave = vi.fn();
    renderForm([{ key: 'summary', label: 'Summary', type: 'text', required: false }], {}, { onSave });
    const field = screen.getByTestId('stage-form-field-summary');
    fireEvent.focus(field);
    fireEvent.change(field, { target: { value: 'Q3 recap' } });
    fireEvent.blur(field);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith({ summary: 'Q3 recap' });
  });
});

describe('StageNodeForm — required badge (mirrors backend _form_incomplete)', () => {
  it('shows the required badge only on required fields', () => {
    renderForm([
      { key: 'summary', label: 'Summary', type: 'text', required: true },
      { key: 'notes', label: 'Notes', type: 'textarea', required: false },
    ]);
    expect(screen.getByTestId('stage-form-required-summary')).toBeInTheDocument();
    expect(screen.queryByTestId('stage-form-required-notes')).toBeNull();
  });

  it('text/textarea/select/date: empty string is unfilled', () => {
    renderForm([{ key: 'summary', label: 'Summary', type: 'text', required: true }], { summary: '' });
    expect(screen.getByTestId('stage-form-required-summary')).toHaveAttribute('data-filled', 'false');
  });

  it('text/textarea/select/date: a missing key is unfilled', () => {
    renderForm([{ key: 'summary', label: 'Summary', type: 'text', required: true }], {});
    expect(screen.getByTestId('stage-form-required-summary')).toHaveAttribute('data-filled', 'false');
  });

  it('text/textarea/select/date: a non-empty string is filled', () => {
    renderForm([{ key: 'summary', label: 'Summary', type: 'text', required: true }], { summary: 'Filled in' });
    expect(screen.getByTestId('stage-form-required-summary')).toHaveAttribute('data-filled', 'true');
  });

  it('number: a missing key is unfilled', () => {
    renderForm([{ key: 'budget', label: 'Budget', type: 'number', required: true }], {});
    expect(screen.getByTestId('stage-form-required-budget')).toHaveAttribute('data-filled', 'false');
  });

  it('number: 0 counts as filled (presence check, not truthiness)', () => {
    renderForm([{ key: 'budget', label: 'Budget', type: 'number', required: true }], { budget: 0 });
    expect(screen.getByTestId('stage-form-required-budget')).toHaveAttribute('data-filled', 'true');
  });

  it('checkbox: false does not satisfy required', () => {
    renderForm([{ key: 'approved', label: 'Approved', type: 'checkbox', required: true }], { approved: false });
    expect(screen.getByTestId('stage-form-required-approved')).toHaveAttribute('data-filled', 'false');
  });

  it('checkbox: a missing key does not satisfy required', () => {
    renderForm([{ key: 'approved', label: 'Approved', type: 'checkbox', required: true }], {});
    expect(screen.getByTestId('stage-form-required-approved')).toHaveAttribute('data-filled', 'false');
  });

  it('checkbox: true satisfies required', () => {
    renderForm([{ key: 'approved', label: 'Approved', type: 'checkbox', required: true }], { approved: true });
    expect(screen.getByTestId('stage-form-required-approved')).toHaveAttribute('data-filled', 'true');
  });

  it('required badge updates live as the user types, before blur/save', () => {
    renderForm([{ key: 'summary', label: 'Summary', type: 'text', required: true }], {});
    const field = screen.getByTestId('stage-form-field-summary');
    expect(screen.getByTestId('stage-form-required-summary')).toHaveAttribute('data-filled', 'false');
    fireEvent.change(field, { target: { value: 'typed but not blurred yet' } });
    expect(screen.getByTestId('stage-form-required-summary')).toHaveAttribute('data-filled', 'true');
  });
});

describe('StageNodeForm — the date field is the shared DateTimePopover', () => {
  const DATE_SCHEMA: FormFieldDef[] = [
    { key: 'due', label: 'Due', type: 'date', required: false },
  ];

  it('keeps its data-testid on the trigger (tests and e2e locate the field by it)', () => {
    renderForm(DATE_SCHEMA);
    const trigger = screen.getByTestId('stage-form-field-due');
    expect(trigger.tagName).toBe('BUTTON');
    // Still the target of the field's own <label>, so it keeps an accessible name.
    expect(trigger.id).toBe('stage-form-field-due');
  });

  it('the trigger opens the popover', () => {
    renderForm(DATE_SCHEMA, { due: '2026-08-15' });
    expect(screen.queryByTestId('date-time-popover')).toBeNull();
    fireEvent.click(screen.getByTestId('stage-form-field-due'));
    expect(screen.getByTestId('date-time-popover')).toBeTruthy();
  });

  it('disabled: the popover cannot be opened and nothing is saved', () => {
    const onSave = vi.fn();
    renderForm(DATE_SCHEMA, { due: '2026-08-15' }, { disabled: true, onSave });
    const trigger = screen.getByTestId('stage-form-field-due');
    expect(trigger).toBeDisabled();
    fireEvent.click(trigger);
    expect(screen.queryByTestId('date-time-popover')).toBeNull();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("Clear saves an empty string, matching the old input's clearable behaviour", () => {
    const onSave = vi.fn();
    renderForm(DATE_SCHEMA, { due: '2026-08-15' }, { onSave });
    fireEvent.click(screen.getByTestId('stage-form-field-due'));
    fireEvent.click(screen.getByTestId('date-time-clear'));
    expect(onSave).toHaveBeenCalledWith({ due: '' });
  });

  it('re-picking the day already held saves nothing (no phantom PATCH)', () => {
    const onSave = vi.fn();
    renderForm(DATE_SCHEMA, { due: '2026-08-15' }, { onSave });
    fireEvent.click(screen.getByTestId('stage-form-field-due'));
    fireEvent.click(screen.getByLabelText('2026-08-15'));
    expect(onSave).not.toHaveBeenCalled();
  });
});
