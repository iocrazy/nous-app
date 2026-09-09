import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ForkRunDialog } from './ForkRunDialog';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, f?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof f === 'string' ? f : k;
      const v = (typeof f === 'object' && f ? f : vars) as Record<string, unknown> | undefined;
      return v ? tpl.replace(/\{\{(\w+)\}\}/g, (_, n) => String(v[n])) : tpl;
    },
  }),
}));
afterEach(cleanup);

describe('ForkRunDialog', () => {
  it('names the step, trims the steer and confirms with it', () => {
    const onConfirm = vi.fn();
    render(<ForkRunDialog stepLabel="step 7" pending={false} error={null} onConfirm={onConfirm} onCancel={vi.fn()} />);
    expect(screen.getByTestId('fork-dialog').textContent).toContain('Fork from step 7');
    fireEvent.change(screen.getByTestId('fork-steer'), { target: { value: '  be darker  ' } });
    fireEvent.click(screen.getByTestId('fork-confirm'));
    expect(onConfirm).toHaveBeenCalledWith('be darker');
  });

  it('an empty steer confirms with undefined', () => {
    const onConfirm = vi.fn();
    render(<ForkRunDialog stepLabel="step 7" pending={false} error={null} onConfirm={onConfirm} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByTestId('fork-steer'), { target: { value: '   ' } });
    fireEvent.click(screen.getByTestId('fork-confirm'));
    expect(onConfirm).toHaveBeenCalledWith(undefined);
  });

  it('pending disables both buttons; error copy is shown', () => {
    render(<ForkRunDialog stepLabel="step 7" pending error="The run is still going" onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect((screen.getByTestId('fork-confirm') as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId('fork-cancel') as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId('fork-error').textContent).toBe('The run is still going');
  });

  it('Escape (window-level), backdrop and Cancel all close; none while pending', () => {
    const onCancel = vi.fn();
    render(<ForkRunDialog stepLabel="step 7" pending={false} error={null} onConfirm={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('fork-backdrop'));
    fireEvent.click(screen.getByTestId('fork-cancel'));
    expect(onCancel).toHaveBeenCalledTimes(3);
    cleanup();
    const onCancel2 = vi.fn();
    render(<ForkRunDialog stepLabel="step 7" pending error={null} onConfirm={vi.fn()} onCancel={onCancel2} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('fork-backdrop'));
    expect(onCancel2).not.toHaveBeenCalled();
  });

  it('focuses the steer box on open and hands focus back on close', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    const { unmount } = render(<ForkRunDialog stepLabel="step 7" pending={false} error={null} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(document.activeElement).toBe(screen.getByTestId('fork-steer'));
    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });
});
