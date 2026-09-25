/**
 * A saved value that became unpickable stays shown — greyed, with its reason.
 *
 * Model pickers mark rows that exist but cannot run right now (a nous-engine
 * model that is authorized but not loaded) as `disabled` + `data-description`.
 * When such a row is ALREADY the saved value, the picker must not swap it out
 * behind the user's back, and it must not look like a normal healthy choice
 * either: the trigger dims it and carries the reason as its title.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import { UiSelect } from './primitives';

function renderPicker(value: string) {
  return render(
    <UiSelect aria-label="Model" value={value} onChange={vi.fn()}>
      <option value="ready">ready-model</option>
      <option value="cold" disabled data-description="Not loaded on nous-engine">
        cold-model
      </option>
    </UiSelect>,
  );
}

describe('UiSelect — disabled option as the current value', () => {
  it('keeps showing it, dimmed, with the reason as the trigger title', () => {
    renderPicker('cold');
    const trigger = screen.getByRole('button', { name: 'Model' });
    const label = trigger.querySelector('[data-selected-label]') as HTMLElement;
    expect(label.textContent).toBe('cold-model');
    expect(label.dataset.unavailable).toBe('true');
    expect(label.className).toContain('opacity-60');
    expect(trigger.getAttribute('title')).toBe('Not loaded on nous-engine');
  });

  it('a normal selection carries no unavailable marking', () => {
    renderPicker('ready');
    const trigger = screen.getByRole('button', { name: 'Model' });
    const label = trigger.querySelector('[data-selected-label]') as HTMLElement;
    expect(label.textContent).toBe('ready-model');
    expect(label.dataset.unavailable).toBeUndefined();
    expect(trigger.getAttribute('title')).toBeNull();
  });

  it('the disabled row cannot be committed from the menu', () => {
    const onChange = vi.fn();
    render(
      <UiSelect aria-label="Model" value="ready" onChange={onChange}>
        <option value="ready">ready-model</option>
        <option value="cold" disabled data-description="Not loaded on nous-engine">
          cold-model
        </option>
      </UiSelect>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Model' }));
    const row = screen.getByRole('option', { name: /cold-model/ });
    expect((row as HTMLButtonElement).disabled).toBe(true);
    // The second-line description is aria-hidden; the reason also rides on
    // the row's title so a hover (and assistive tech) can read it.
    expect(row.getAttribute('title')).toBe('Not loaded on nous-engine');
    fireEvent.click(row);
    expect(onChange).not.toHaveBeenCalled();
  });
});
