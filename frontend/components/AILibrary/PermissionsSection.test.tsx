import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PermissionsSection from './PermissionsSection';
import type { AgentCapabilities } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

afterEach(cleanup);

/** Render with the capability half wired to a spy; chat half kept inert. */
function renderCaps(capabilities: AgentCapabilities) {
  const onCapabilitiesChange = vi.fn();
  render(
    <PermissionsSection
      value={{}}
      onChange={vi.fn()}
      capabilities={capabilities}
      onCapabilitiesChange={onCapabilitiesChange}
    />,
  );
  return { onCapabilitiesChange };
}

const writeRadio = (level: string) =>
  screen.getByRole('radio', { name: `aiLibrary.permissions.writeLevel_${level}` });

const capToggle = (key: string) =>
  screen.getByRole('switch', { name: new RegExp(`aiLibrary.permissions.${key}`) });

describe('PermissionsSection — capability grants', () => {
  it('reflects an all-denied agent as the fail-closed default', () => {
    renderCaps({});
    // 'none' selected, not merely "nothing selected" — the revoke tier is a
    // real, visible state.
    expect(writeRadio('none')).toHaveAttribute('aria-checked', 'true');
    expect(writeRadio('write')).toHaveAttribute('aria-checked', 'false');
    for (const k of ['deleteCap', 'generateImage', 'generateVideo', 'crossEpisodeRead', 'externalPublish']) {
      expect(capToggle(k)).toHaveAttribute('aria-checked', 'false');
    }
  });

  it('renders the grants an agent already has', () => {
    renderCaps({
      write_level: 'propose',
      delete: true,
      media: { image: true, max_calls_per_turn: 2 },
      cross_episode_read: true,
    });
    expect(writeRadio('propose')).toHaveAttribute('aria-checked', 'true');
    expect(capToggle('deleteCap')).toHaveAttribute('aria-checked', 'true');
    expect(capToggle('generateImage')).toHaveAttribute('aria-checked', 'true');
    expect(capToggle('generateVideo')).toHaveAttribute('aria-checked', 'false');
    expect(capToggle('crossEpisodeRead')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByLabelText('aiLibrary.permissions.mediaCap')).toHaveValue(2);
  });

  it('emits the exact write_level literal the backend accepts', () => {
    const { onCapabilitiesChange } = renderCaps({});
    fireEvent.click(writeRadio('write'));
    expect(onCapabilitiesChange).toHaveBeenCalledWith({ write_level: 'write' });
  });

  it('can revoke back down to none', () => {
    const { onCapabilitiesChange } = renderCaps({ write_level: 'write' });
    fireEvent.click(writeRadio('none'));
    expect(onCapabilitiesChange).toHaveBeenCalledWith({ write_level: 'none' });
  });

  it('emits real booleans, never truthy strings', () => {
    const { onCapabilitiesChange } = renderCaps({});
    fireEvent.click(capToggle('deleteCap'));
    // The backend rejects "yes"/1 as a grant (StrictBool), so the UI must
    // never produce anything but a literal boolean.
    expect(onCapabilitiesChange).toHaveBeenCalledWith({ delete: true });
    expect(typeof onCapabilitiesChange.mock.calls[0][0].delete).toBe('boolean');
  });

  it('toggling one media kind preserves the other and the cap', () => {
    const { onCapabilitiesChange } = renderCaps({
      media: { video: true, max_calls_per_turn: 1 },
    });
    fireEvent.click(capToggle('generateImage'));
    expect(onCapabilitiesChange).toHaveBeenCalledWith({
      media: { video: true, max_calls_per_turn: 1, image: true },
    });
  });

  it('clamps the per-turn cap into the range the backend accepts', () => {
    const { onCapabilitiesChange } = renderCaps({ media: { image: true } });
    const input = screen.getByLabelText('aiLibrary.permissions.mediaCap');

    fireEvent.change(input, { target: { value: '-5' } });
    expect(onCapabilitiesChange).toHaveBeenLastCalledWith({
      media: { image: true, max_calls_per_turn: 0 },
    });

    fireEvent.change(input, { target: { value: '9999' } });
    expect(onCapabilitiesChange).toHaveBeenLastCalledWith({
      media: { image: true, max_calls_per_turn: 100 },
    });
  });

  it('never emits NaN when the cap field is cleared', () => {
    const { onCapabilitiesChange } = renderCaps({ media: { image: true } });
    fireEvent.change(screen.getByLabelText('aiLibrary.permissions.mediaCap'), {
      target: { value: '' },
    });
    const sent = onCapabilitiesChange.mock.calls.at(-1)?.[0].media.max_calls_per_turn;
    expect(Number.isNaN(sent)).toBe(false);
    expect(sent).toBe(4);
  });

  it('leaves untouched dimensions absent so a PATCH cannot revoke by omission', () => {
    const { onCapabilitiesChange } = renderCaps({ write_level: 'propose' });
    fireEvent.click(capToggle('externalPublish'));
    const sent = onCapabilitiesChange.mock.calls[0][0];
    expect(sent).toEqual({ write_level: 'propose', external_publish: true });
    expect('delete' in sent).toBe(false);
  });
});
