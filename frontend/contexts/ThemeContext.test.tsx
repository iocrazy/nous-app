import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { ThemeProvider, useTheme } from './ThemeContext';

function Probe() {
  const { preference, resolved, setPreference } = useTheme();
  return (
    <div>
      <span data-testid="pref">{preference}</span>
      <span data-testid="resolved">{resolved}</span>
      <button onClick={() => setPreference('light')}>go-light</button>
    </div>
  );
}

describe('ThemeContext', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: false, // system = dark
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
  });

  it('defaults to system (D11) and writes resolved data-theme', () => {
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByTestId('pref').textContent).toBe('system');
    expect(screen.getByTestId('resolved').textContent).toBe('dark'); // mocked matchMedia = dark
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it("saved 'system' preference resolves via prefers-color-scheme", () => {
    localStorage.setItem('mediahub.theme', 'system');
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByTestId('pref').textContent).toBe('system');
    expect(screen.getByTestId('resolved').textContent).toBe('dark'); // mocked matchMedia = dark
  });

  it('setPreference(light) persists and flips data-theme', () => {
    render(<ThemeProvider><Probe /></ThemeProvider>);
    fireEvent.click(screen.getByText('go-light'));
    expect(localStorage.getItem('mediahub.theme')).toBe('light');
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});
