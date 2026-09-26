import { useEffect } from 'react';

import { useGlobalChatStore } from '../../stores/globalChatStore';
import { deriveFabActivity } from './fabActivity';

/**
 * Mirror the chat panel's state onto the floating mascot.
 *
 * On unmount a `running` activity drops to `idle`: the panel owns the stream,
 * so once it is gone nothing is running on this client any more. `waiting`
 * survives — the question is still parked server-side and the mascot keeps
 * asking for an answer.
 */
export function useFabActivityPublisher(
  sending: boolean,
  lastAssistantAwaitingInput: boolean,
): void {
  useEffect(() => {
    useGlobalChatStore
      .getState()
      .setFabActivity(deriveFabActivity({ sending, lastAssistantAwaitingInput }));
  }, [sending, lastAssistantAwaitingInput]);

  useEffect(
    () => () => {
      const store = useGlobalChatStore.getState();
      if (store.fabActivity === 'running') store.setFabActivity('idle');
    },
    [],
  );
}
