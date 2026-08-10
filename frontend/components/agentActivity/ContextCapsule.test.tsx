/**
 * Design §D repaints this capsule from info-* (steel blue) to ok-* (green).
 * The color assertion here is the RED half of Task 5 — everything else pins
 * existing behaviour (rendering, signal precedence, dismiss, expand).
 */

import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ContextCapsule } from './ContextCapsule';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string, opts?: Record<string, unknown>) => {
      if (typeof fallback !== 'string') return key;
      if (!opts) return fallback;
      return fallback.replace(/\{\{(\w+)\}\}/g, (_match, name: string) =>
        String(opts[name] ?? ''),
      );
    },
  }),
}));

describe('ContextCapsule', () => {
  it('shows the scene label in the title when provided', () => {
    const { getByTestId } = render(
      <ContextCapsule
        value={{ text: 'INT. HOUSE - DAY', sceneLabel: 'S2' }}
        onDismiss={vi.fn()}
      />,
    );
    expect(getByTestId('context-capsule-toggle').textContent).toContain(
      'Selection from S2',
    );
  });

  it('falls back to a generic title with no scene label', () => {
    const { getByTestId } = render(
      <ContextCapsule value={{ text: 'some text' }} onDismiss={vi.fn()} />,
    );
    const label = getByTestId('context-capsule-toggle').textContent;
    expect(label).toContain('Selection');
    expect(label).not.toContain('Selection from');
  });

  it('shows the element type as the signal', () => {
    const { getByTestId } = render(
      <ContextCapsule
        value={{ text: 'text', sceneLabel: 'S2', elementType: 'dialogue' }}
        onDismiss={vi.fn()}
      />,
    );
    expect(getByTestId('context-capsule-toggle').textContent).toContain('dialogue');
  });

  it('prefers the cross-scene signal over the element type', () => {
    const { getByTestId } = render(
      <ContextCapsule
        value={{
          text: 'text',
          sceneLabel: 'S2',
          elementType: 'dialogue',
          crossScene: true,
        }}
        onDismiss={vi.fn()}
      />,
    );
    const label = getByTestId('context-capsule-toggle').textContent;
    expect(label).toContain('multiple scenes');
    expect(label).not.toContain('dialogue');
  });

  it('calls onDismiss when the close button is clicked', () => {
    const onDismiss = vi.fn();
    const { getByTestId } = render(
      <ContextCapsule value={{ text: 'text' }} onDismiss={onDismiss} />,
    );
    fireEvent.click(getByTestId('context-capsule-dismiss'));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('reveals the original text only after the toggle is clicked', () => {
    const { getByTestId, queryByTestId } = render(
      <ContextCapsule value={{ text: 'the full quoted passage' }} onDismiss={vi.fn()} />,
    );
    expect(queryByTestId('context-capsule-text')).toBeNull();
    fireEvent.click(getByTestId('context-capsule-toggle'));
    expect(getByTestId('context-capsule-text').textContent).toBe('the full quoted passage');
  });

  it('uses the ok-* semantic tokens, not the retired info-* ones (design §D repaint)', () => {
    const { getByTestId } = render(
      <ContextCapsule value={{ text: 'text' }} onDismiss={vi.fn()} />,
    );

    const wrapper = getByTestId('context-capsule');
    expect(wrapper.className).toContain('border-ok-line');
    expect(wrapper.className).toContain('bg-ok-soft');
    expect(wrapper.className).not.toMatch(/\binfo-/);

    const toggle = getByTestId('context-capsule-toggle');
    expect(toggle.className).toContain('text-ok');
    expect(toggle.className).not.toContain('text-info');

    fireEvent.click(toggle);
    const text = getByTestId('context-capsule-text');
    expect(text.className).toContain('border-ok-line');
    expect(text.className).not.toMatch(/\binfo-/);
  });
});
