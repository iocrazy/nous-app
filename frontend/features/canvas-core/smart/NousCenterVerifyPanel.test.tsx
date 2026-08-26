import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { NousCenterVerifyPanel } from './NousCenterVerifyPanel';

import en from '../../../public/locales/en.json';

// Resolve against the REAL shipped English copy rather than a hand-written
// table, so a missing/renamed key surfaces here as a failing assertion instead
// of a raw `aiSettings.someKey` reaching users. The component's own i18n
// instance is never initialized in tests (nothing loads i18n.ts), so
// react-i18next would otherwise hand back bare keys.
vi.mock('react-i18next', () => {
  // Created once by the factory so `t` is referentially stable across renders,
  // exactly like the real react-i18next hook. An unstable `t` would make any
  // hook that lists it as a dependency re-fire on every render.
  const t = (key: string, vars?: Record<string, unknown>): string => {
    const template = key
      .split('.')
      .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
    if (typeof template !== 'string') return key;
    return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars?.[name] ?? ''));
  };
  return { useTranslation: () => ({ t }) };
});

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ success: true, data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('NousCenterVerifyPanel', () => {
  it('renders the trigger button', () => {
    render(<NousCenterVerifyPanel />);
    expect(
      screen.getByRole('button', { name: /verify protocol/i }),
    ).toBeInTheDocument();
  });

  it('shows a success row when the endpoint says ok=true', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({
        ok: true,
        base_url: 'https://nous.test',
        workflows_visible: 3,
      }),
    );
    render(<NousCenterVerifyPanel />);
    fireEvent.click(screen.getByRole('button', { name: /verify protocol/i }));
    await waitFor(() =>
      expect(screen.getByText(/service reachable/i)).toBeInTheDocument(),
    );
    expect(screen.getByText('https://nous.test')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('shows a failure row when the endpoint says ok=false', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({ ok: false, error: 'nous-center is not configured (...)' }),
    );
    render(<NousCenterVerifyPanel />);
    fireEvent.click(screen.getByRole('button', { name: /verify protocol/i }));
    await waitFor(() =>
      expect(screen.getByText(/verification failed/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/not configured/i)).toBeInTheDocument();
  });

  it('includes the status_code when present in the failure payload', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({ ok: false, status_code: 401, error: 'HTTP 401: bad token' }),
    );
    render(<NousCenterVerifyPanel />);
    fireEvent.click(screen.getByRole('button', { name: /verify protocol/i }));
    await waitFor(() =>
      expect(screen.getByText(/HTTP 401: bad token/)).toBeInTheDocument(),
    );
  });

  it('button is disabled while probing', async () => {
    let resolveFetch: ((value: Response) => void) | null = null;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    render(<NousCenterVerifyPanel />);
    const btn = screen.getByRole('button', { name: /verify protocol/i });
    fireEvent.click(btn);
    await waitFor(() => expect(btn).toBeDisabled());
    expect(btn).toHaveTextContent(/verifying/i);
    resolveFetch?.(envelope({ ok: true, base_url: 'x', workflows_visible: 0 }));
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it('surfaces a network-level rejection as a failure row', async () => {
    fetchMock.mockRejectedValueOnce(new Error('network down'));
    render(<NousCenterVerifyPanel />);
    fireEvent.click(screen.getByRole('button', { name: /verify protocol/i }));
    await waitFor(() =>
      expect(screen.getByText(/network down/i)).toBeInTheDocument(),
    );
  });
});
