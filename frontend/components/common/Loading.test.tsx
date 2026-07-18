import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { Loading } from './Loading';

afterEach(() => cleanup());

describe('Loading', () => {
  it('renders an accessible status with a three-dot pulse in currentColor', () => {
    render(<Loading />);
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-busy', 'true');
    const dots = status.querySelectorAll('.animate-pulse');
    expect(dots).toHaveLength(3);
    // currentColor so it adapts to the app ink palette AND the editor's scoped theme.
    dots.forEach((d) => expect(d.classList.contains('bg-current')).toBe(true));
  });

  it('shows the caption when a label is given, and none otherwise', () => {
    const { rerender } = render(<Loading label="Loading Scenes…" />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading Scenes…');
    rerender(<Loading />);
    expect(screen.getByRole('status').textContent).toBe('');
  });

  it('center mode fills and centres its box; inline mode does not', () => {
    const { rerender } = render(<Loading center />);
    expect(screen.getByRole('status').className).toContain('justify-center');
    rerender(<Loading />);
    expect(screen.getByRole('status').className).toContain('inline-flex');
    expect(screen.getByRole('status').className).not.toContain('justify-center');
  });
});
