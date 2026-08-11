import { render, screen, within, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PermissionsSection from './PermissionsSection';
import type { AgentCapabilities, AgentChatPermissions } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

afterEach(cleanup);

/** Render with the capability half wired to a spy; chat half kept inert unless overridden. */
function renderCaps(capabilities: AgentCapabilities, value: AgentChatPermissions = {}) {
  const onCapabilitiesChange = vi.fn();
  const onChange = vi.fn();
  render(
    <PermissionsSection
      value={value}
      onChange={onChange}
      capabilities={capabilities}
      onCapabilitiesChange={onCapabilitiesChange}
    />,
  );
  return { onCapabilitiesChange, onChange };
}

const writeRadio = (level: string) =>
  screen.getByRole('radio', { name: `aiLibrary.permissions.writeLevel_${level}` });

const capToggle = (key: string) =>
  screen.getByRole('switch', { name: new RegExp(`aiLibrary.permissions.${key}`) });

describe('PermissionsSection — grouping', () => {
  it('renders the two group containers', () => {
    renderCaps({});
    expect(screen.getByTestId('perm-group-scope')).toBeInTheDocument();
    expect(screen.getByTestId('perm-group-capabilities')).toBeInTheDocument();
  });

  it('places crossEpisodeRead inside the scope group, not capabilities', () => {
    renderCaps({});
    const scopeGroup = screen.getByTestId('perm-group-scope');
    const capsGroup = screen.getByTestId('perm-group-capabilities');
    expect(
      within(scopeGroup).getByRole('switch', { name: /crossEpisodeRead/ }),
    ).toBeInTheDocument();
    expect(
      within(capsGroup).queryByRole('switch', { name: /crossEpisodeRead/ }),
    ).not.toBeInTheDocument();
  });

  it('does not grey out crossEpisodeRead when chat is disabled — it does not depend on chat', () => {
    renderCaps({}, { enabled: false });
    const scopeGroup = screen.getByTestId('perm-group-scope');
    const toggle = within(scopeGroup).getByRole('switch', { name: /crossEpisodeRead/ });
    // The chat-only items (readTeamFiles/autoBroadcast) sit behind a
    // pointer-events-none gate when chat is off; crossEpisodeRead must not.
    const gate = screen.queryByTestId('chat-enabled-gate');
    expect(gate).toBeInTheDocument();
    expect(gate).not.toContainElement(toggle);
  });

  it('still writes cross_episode_read on the capabilities object when toggled', () => {
    const { onCapabilitiesChange } = renderCaps({}, { enabled: false });
    fireEvent.click(
      within(screen.getByTestId('perm-group-scope')).getByRole('switch', {
        name: /crossEpisodeRead/,
      }),
    );
    expect(onCapabilitiesChange).toHaveBeenCalledWith({ cross_episode_read: true });
  });
});

describe('PermissionsSection — dead toggles removed', () => {
  it('no longer renders deleteCap', () => {
    renderCaps({ delete: true });
    expect(screen.queryByRole('switch', { name: /deleteCap/ })).not.toBeInTheDocument();
    expect(screen.queryByText('aiLibrary.permissions.deleteCap')).not.toBeInTheDocument();
    expect(screen.queryByText('aiLibrary.permissions.deleteCapDesc')).not.toBeInTheDocument();
  });

  it('no longer renders externalPublish', () => {
    renderCaps({ external_publish: true });
    expect(screen.queryByRole('switch', { name: /externalPublish/ })).not.toBeInTheDocument();
    expect(screen.queryByText('aiLibrary.permissions.externalPublish')).not.toBeInTheDocument();
    expect(screen.queryByText('aiLibrary.permissions.externalPublishDesc')).not.toBeInTheDocument();
  });
});

describe('PermissionsSection — media cap disabled state', () => {
  it('disables #media-cap when neither image nor video generation is on', () => {
    renderCaps({});
    expect(screen.getByLabelText('aiLibrary.permissions.mediaCap')).toBeDisabled();
  });

  it('enables #media-cap once image generation is granted', () => {
    renderCaps({ media: { image: true } });
    expect(screen.getByLabelText('aiLibrary.permissions.mediaCap')).not.toBeDisabled();
  });

  it('enables #media-cap once video generation is granted', () => {
    renderCaps({ media: { video: true } });
    expect(screen.getByLabelText('aiLibrary.permissions.mediaCap')).not.toBeDisabled();
  });
});

describe('PermissionsSection — capability grants', () => {
  it('reflects an all-denied agent as the fail-closed default', () => {
    renderCaps({});
    // 'none' selected, not merely "nothing selected" — the revoke tier is a
    // real, visible state.
    expect(writeRadio('none')).toHaveAttribute('aria-checked', 'true');
    expect(writeRadio('write')).toHaveAttribute('aria-checked', 'false');
    for (const k of ['generateImage', 'generateVideo', 'crossEpisodeRead']) {
      expect(capToggle(k)).toHaveAttribute('aria-checked', 'false');
    }
  });

  it('renders the grants an agent already has', () => {
    renderCaps({
      write_level: 'propose',
      media: { image: true, max_calls_per_turn: 2 },
      cross_episode_read: true,
    });
    expect(writeRadio('propose')).toHaveAttribute('aria-checked', 'true');
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
    fireEvent.click(capToggle('generateImage'));
    // The backend rejects "yes"/1 as a grant (StrictBool), so the UI must
    // never produce anything but a literal boolean.
    expect(onCapabilitiesChange).toHaveBeenCalledWith({ media: { image: true } });
    expect(typeof onCapabilitiesChange.mock.calls[0][0].media.image).toBe('boolean');
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
    fireEvent.click(capToggle('crossEpisodeRead'));
    const sent = onCapabilitiesChange.mock.calls[0][0];
    expect(sent).toEqual({ write_level: 'propose', cross_episode_read: true });
    expect('media' in sent).toBe(false);
  });
});
