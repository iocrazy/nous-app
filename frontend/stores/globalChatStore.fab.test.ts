import { beforeEach, describe, expect, it } from 'vitest';

import { useGlobalChatStore } from './globalChatStore';

describe('globalChatStore · collapsed FAB state', () => {
  beforeEach(() => {
    useGlobalChatStore.setState({
      open: false,
      fabSide: 'right',
      fabTop: null,
      fabActivity: 'idle',
      fabUnread: 0,
    });
  });

  it('defaults to the right edge with no remembered top', () => {
    const s = useGlobalChatStore.getState();
    expect(s.fabSide).toBe('right');
    expect(s.fabTop).toBeNull();
  });

  it('stores side and top together', () => {
    useGlobalChatStore.getState().setFabPosition({ side: 'left', top: 240 });
    const s = useGlobalChatStore.getState();
    expect(s.fabSide).toBe('left');
    expect(s.fabTop).toBe(240);
  });

  it('persists side and top but not activity or unread', () => {
    const partialize = useGlobalChatStore.persist.getOptions().partialize;
    expect(partialize).toBeTypeOf('function');
    useGlobalChatStore.setState({ fabSide: 'left', fabTop: 123, fabActivity: 'running', fabUnread: 3 });
    const persisted = partialize!(useGlobalChatStore.getState()) as Record<string, unknown>;
    expect(persisted.fabSide).toBe('left');
    expect(persisted.fabTop).toBe(123);
    expect(persisted).not.toHaveProperty('fabActivity');
    expect(persisted).not.toHaveProperty('fabUnread');
  });

  it('clears unread when the window opens', () => {
    useGlobalChatStore.getState().setFabUnread(4);
    useGlobalChatStore.getState().setOpen(true);
    expect(useGlobalChatStore.getState().fabUnread).toBe(0);
    useGlobalChatStore.getState().setOpen(false);
    useGlobalChatStore.getState().setFabUnread(2);
    useGlobalChatStore.getState().toggle();
    expect(useGlobalChatStore.getState().open).toBe(true);
    expect(useGlobalChatStore.getState().fabUnread).toBe(0);
  });

  it('never stores a negative unread count', () => {
    useGlobalChatStore.getState().setFabUnread(-3);
    expect(useGlobalChatStore.getState().fabUnread).toBe(0);
  });
});
