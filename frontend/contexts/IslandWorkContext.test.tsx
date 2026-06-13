import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { IslandWorkProvider, useIslandWork } from './IslandWorkContext';

function Probe() {
  const { infoVisible, setInfoVisible, infoWidth, setInfoWidth, infoAvailable, setInfoAvailable } = useIslandWork();
  return (
    <div>
      <span data-testid="vis">{String(infoVisible)}</span>
      <span data-testid="w">{infoWidth}</span>
      <span data-testid="avail">{String(infoAvailable)}</span>
      <button onClick={() => setInfoVisible(true)}>show</button>
      <button onClick={() => setInfoWidth(100)}>narrow</button>
      <button onClick={() => setInfoWidth(999)}>wide</button>
      <button onClick={() => setInfoAvailable(true)}>avail</button>
    </div>
  );
}

describe('IslandWorkContext', () => {
  it('inert defaults when no provider (classic mode safe)', () => {
    render(<Probe />);
    expect(screen.getByTestId('vis').textContent).toBe('false');
    expect(screen.getByTestId('avail').textContent).toBe('false');
  });
  it('provider tracks info availability (default false)', () => {
    render(<IslandWorkProvider><Probe /></IslandWorkProvider>);
    expect(screen.getByTestId('avail').textContent).toBe('false');
    fireEvent.click(screen.getByText('avail'));
    expect(screen.getByTestId('avail').textContent).toBe('true');
  });
  it('provider tracks visibility', () => {
    render(<IslandWorkProvider><Probe /></IslandWorkProvider>);
    fireEvent.click(screen.getByText('show'));
    expect(screen.getByTestId('vis').textContent).toBe('true');
  });
  it('default infoWidth is 360 (spec §2)', () => {
    render(<IslandWorkProvider><Probe /></IslandWorkProvider>);
    expect(screen.getByTestId('w').textContent).toBe('360');
  });
  it('clamps infoWidth to 250–480 (spec §2)', () => {
    render(<IslandWorkProvider><Probe /></IslandWorkProvider>);
    fireEvent.click(screen.getByText('narrow'));
    expect(screen.getByTestId('w').textContent).toBe('250');
    fireEvent.click(screen.getByText('wide'));
    expect(screen.getByTestId('w').textContent).toBe('480');
  });
});
