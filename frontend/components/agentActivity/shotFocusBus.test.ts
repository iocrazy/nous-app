import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  hasShotFocusListener,
  onShotFocus,
  onStoryboardRefresh,
  requestShotFocus,
  requestStoryboardRefresh,
  setShotFocusConsumerActive,
} from './shotFocusBus';

// `consumerActive` is module-level state (Task 7 review round 1) — reset it
// after every test so a test that forgets to flip it back doesn't leak into
// the next one (mirrors how `listeners`/`refreshListeners` are already kept
// clean by each test's own unsubscribe call).
afterEach(() => {
  setShotFocusConsumerActive(true);
});

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

// Task 7 (shot-nodes-on-canvas epic — keep-alive 显隐切换) review round 1:
// `hasShotFocusListener()` must mean "can act on a request right now", not
// merely "has a handler registered" — a subscriber can now stay mounted
// while hidden (see `EpisodeStoryboardPage`'s `active` prop).
describe('shotFocusBus — active-aware hasShotFocusListener (Task 7 review round 1)', () => {
  it('defaults consumerActive to true — a bare subscription still reports available (no regression)', () => {
    const unsubscribe = onShotFocus(vi.fn());
    expect(hasShotFocusListener()).toBe(true);
    unsubscribe();
  });

  it('reports false once the subscriber marks itself inactive, even though it is still subscribed', () => {
    const listener = vi.fn();
    const unsubscribe = onShotFocus(listener);
    expect(hasShotFocusListener()).toBe(true);

    setShotFocusConsumerActive(false);
    expect(hasShotFocusListener()).toBe(false);

    // Still subscribed — a request delivered directly still reaches the
    // listener (dropping is EpisodeStoryboardPage's own `active` guard
    // inside the callback, not the bus's job) — `hasShotFocusListener` only
    // changes what a CALLER decides to render, it doesn't itself gate
    // delivery.
    requestShotFocus('7');
    expect(listener).toHaveBeenCalledWith('7');

    unsubscribe();
  });

  it('reports true again once the subscriber marks itself active', () => {
    const unsubscribe = onShotFocus(vi.fn());
    setShotFocusConsumerActive(false);
    expect(hasShotFocusListener()).toBe(false);

    setShotFocusConsumerActive(true);
    expect(hasShotFocusListener()).toBe(true);

    unsubscribe();
  });

  it('stays false with no subscriber regardless of consumerActive (subscription is still required)', () => {
    setShotFocusConsumerActive(true);
    expect(hasShotFocusListener()).toBe(false);
  });
});

describe('storyboard refresh channel', () => {
  it('does not throw when requested with no subscribers', () => {
    expect(() => requestStoryboardRefresh()).not.toThrow();
  });

  it('notifies a subscribed listener', () => {
    const listener = vi.fn();
    const unsubscribe = onStoryboardRefresh(listener);
    requestStoryboardRefresh();
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it('stops notifying after unsubscribe', () => {
    const listener = vi.fn();
    const unsubscribe = onStoryboardRefresh(listener);
    unsubscribe();
    requestStoryboardRefresh();
    expect(listener).not.toHaveBeenCalled();
  });
});
