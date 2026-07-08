import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const listTokens = vi.fn();
const createToken = vi.fn();
const revokeToken = vi.fn();
vi.mock('../../../services/inspirationService', () => ({
  listTokens: (...a: unknown[]) => listTokens(...a),
  createToken: (...a: unknown[]) => createToken(...a),
  revokeToken: (...a: unknown[]) => revokeToken(...a),
}));

const addToast = vi.fn();
vi.mock('../../Toast', () => ({
  useToast: () => ({ addToast }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));

import { ApiTokensPanel } from '../ApiTokensPanel';

const writeText = vi.fn().mockResolvedValue(undefined);

function row(over: Record<string, unknown> = {}) {
  return {
    id: 't1',
    name: 'iphone-shortcut',
    last_used_at: null,
    created_at: '2026-07-01T10:00:00Z',
    revoked_at: null,
    ...over,
  };
}

describe('ApiTokensPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
  });

  it('empty state → create → reveals one-time secret and copies it', async () => {
    listTokens.mockResolvedValue([]);
    createToken.mockResolvedValue({
      id: 't9',
      name: 'my-script',
      last_used_at: null,
      created_at: '2026-07-07T00:00:00Z',
      revoked_at: null,
      token: 'mhk_secret_abc123',
    });

    render(<ApiTokensPanel />);
    expect(await screen.findByText('No API tokens yet')).toBeTruthy();

    fireEvent.click(screen.getByText('New Token'));
    fireEvent.change(screen.getByPlaceholderText('Token name (e.g. iphone-shortcut)'), {
      target: { value: 'my-script' },
    });
    fireEvent.click(screen.getByText('Create'));

    await waitFor(() => expect(createToken).toHaveBeenCalledWith('my-script'));
    expect(await screen.findByText('mhk_secret_abc123')).toBeTruthy();
    expect(screen.getByText('This token is shown only once. Store it now.')).toBeTruthy();

    fireEvent.click(screen.getByText('Copy'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('mhk_secret_abc123'));
    expect(addToast).toHaveBeenCalledWith('Copied', 'success');
  });

  it('renders a null last_used_at as "Never used"', async () => {
    listTokens.mockResolvedValue([row({ last_used_at: null })]);
    render(<ApiTokensPanel />);
    expect(await screen.findByText('iphone-shortcut')).toBeTruthy();
    expect(screen.getByText('Never used')).toBeTruthy();
  });

  it('revoke → confirm removes the row and calls revokeToken', async () => {
    listTokens.mockResolvedValue([row()]);
    revokeToken.mockResolvedValue(undefined);
    render(<ApiTokensPanel />);

    expect(await screen.findByText('iphone-shortcut')).toBeTruthy();
    fireEvent.click(screen.getByText('Revoke'));
    fireEvent.click(screen.getByText('Confirm'));

    await waitFor(() => expect(revokeToken).toHaveBeenCalledWith('t1'));
    await waitFor(() => expect(screen.queryByText('iphone-shortcut')).toBeNull());
    expect(addToast).toHaveBeenCalledWith('Token revoked', 'success');
  });

  it('create failure toasts an error and adds no row', async () => {
    listTokens.mockResolvedValue([]);
    createToken.mockRejectedValue(new Error('502'));

    render(<ApiTokensPanel />);
    expect(await screen.findByText('No API tokens yet')).toBeTruthy();

    fireEvent.click(screen.getByText('New Token'));
    fireEvent.change(screen.getByPlaceholderText('Token name (e.g. iphone-shortcut)'), {
      target: { value: 'boom' },
    });
    fireEvent.click(screen.getByText('Create'));

    await waitFor(() => expect(addToast).toHaveBeenCalledWith('Failed to create token', 'error'));
    // No secret reveal, list stays empty.
    expect(screen.queryByText('This token is shown only once. Store it now.')).toBeNull();
    expect(screen.getByText('No API tokens yet')).toBeTruthy();
  });
});
