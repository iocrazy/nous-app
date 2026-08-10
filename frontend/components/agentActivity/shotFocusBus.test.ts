import { describe, expect, it, vi } from 'vitest';

import { hasShotFocusListener, onShotFocus, requestShotFocus } from './shotFocusBus';

describe('shotFocusBus', () => {
  it('reports no listener and no-ops safely when nothing is subscribed', () => {
    expect(hasShotFocusListener()).toBe(false);
    expect(() => requestShotFocus('42')).not.toThrow();
  });

  it('delivers the shot id to a subscribed listener', () => {
    const listener = vi.fn();
    const unsubscribe = onShotFocus(listener);
    expect(hasShotFocusListener()).toBe(true);
    requestShotFocus('7');
    expect(listener).toHaveBeenCalledWith('7');
    unsubscribe();
  });

  it('stops delivering after unsubscribe', () => {
    const listener = vi.fn();
    const unsubscribe = onShotFocus(listener);
    unsubscribe();
    expect(hasShotFocusListener()).toBe(false);
    requestShotFocus('7');
    expect(listener).not.toHaveBeenCalled();
  });

  it('delivers to every subscribed listener', () => {
    const a = vi.fn();
    const b = vi.fn();
    const unsubA = onShotFocus(a);
    const unsubB = onShotFocus(b);
    requestShotFocus('3');
    expect(a).toHaveBeenCalledWith('3');
    expect(b).toHaveBeenCalledWith('3');
    unsubA();
    unsubB();
  });

  it('keeps delivering to other listeners when one throws', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const bad = vi.fn(() => {
      throw new Error('boom');
    });
    const good = vi.fn();
    const unsubBad = onShotFocus(bad);
    const unsubGood = onShotFocus(good);

    expect(() => requestShotFocus('5')).not.toThrow();
    expect(good).toHaveBeenCalledWith('5');
    expect(consoleError).toHaveBeenCalled();

    unsubBad();
    unsubGood();
    consoleError.mockRestore();
  });
});
