import { fireEvent, screen } from '@testing-library/react';

/**
 * Test helpers for the shared `UiSelect` primitive.
 *
 * UiSelect renders a styled trigger button (which owns the accessible label)
 * plus an `aria-hidden` native `<select>` mirror as a sibling, and a portal
 * listbox that only exists while open. Tests written for the old native
 * `<select>` (reading `.options`/`.value`, firing `change`) should target the
 * mirror; tests that assert the visible menu should click the portal options.
 */

/** The hidden native `<select>` mirror behind a UiSelect trigger. */
export function uiSelectMirror(accessibleName: string | RegExp): HTMLSelectElement {
  const trigger = screen.getByLabelText(accessibleName);
  const mirror = trigger.parentElement?.querySelector('select');
  if (!mirror) {
    throw new Error(`uiSelectMirror: no native <select> found for "${accessibleName}"`);
  }
  return mirror as HTMLSelectElement;
}

/** Change a UiSelect's value the way a form would — fires its onChange. */
export function changeUiSelect(accessibleName: string | RegExp, value: string): void {
  fireEvent.change(uiSelectMirror(accessibleName), { target: { value } });
}

/** Open a UiSelect and click one of its portal options by visible text. */
export function pickUiSelectOption(
  accessibleName: string | RegExp,
  optionName: string | RegExp,
): void {
  fireEvent.click(screen.getByLabelText(accessibleName));
  fireEvent.click(screen.getByRole('option', { name: optionName }));
}
