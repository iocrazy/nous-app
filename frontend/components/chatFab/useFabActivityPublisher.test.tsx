import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { useGlobalChatStore } from '../../stores/globalChatStore';
import { useFabActivityPublisher } from './useFabActivityPublisher';

describe('useFabActivityPublisher', () => {
  beforeEach(() => {
    useGlobalChatStore.setState({ fabActivity: 'idle' });
  });

  it('publishes running while a turn is sending', () => {
    renderHook(() => useFabActivityPublisher(true, false));
    expect(useGlobalChatStore.getState().fabActivity).toBe('running');
  });

  it('publishes waiting for an unanswered question and idle once cleared', () => {
    const { rerender } = renderHook(
      ({ sending, awaiting }) => useFabActivityPublisher(sending, awaiting),
      { initialProps: { sending: false, awaiting: true } },
    );
    expect(useGlobalChatStore.getState().fabActivity).toBe('waiting');
    rerender({ sending: false, awaiting: false });
    expect(useGlobalChatStore.getState().fabActivity).toBe('idle');
  });

  it('drops running to idle on unmount', () => {
    const { unmount } = renderHook(() => useFabActivityPublisher(true, false));
    unmount();
    expect(useGlobalChatStore.getState().fabActivity).toBe('idle');
  });

  it('keeps waiting on unmount because the question is still parked server-side', () => {
    const { unmount } = renderHook(() => useFabActivityPublisher(false, true));
    unmount();
    expect(useGlobalChatStore.getState().fabActivity).toBe('waiting');
  });
});
