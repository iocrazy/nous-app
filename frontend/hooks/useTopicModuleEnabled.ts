import { useEffect, useState } from 'react';
import { getModuleStatus, type TopicModuleStatus } from '../services/topicService';

/**
 * Topic Inspiration module switches (admin-controlled).
 *
 * - `visible` — display switch: gates the nav item + page routing.
 * - `enabled` — processing switch: when off the page shows a "updates
 *   paused" notice but stays reachable.
 *
 * Both default to `true` while loading / on error so a transient failure
 * never hides the feature or flashes a bogus paused notice.
 */
export function useTopicModuleStatus(): TopicModuleStatus {
  const [status, setStatus] = useState<TopicModuleStatus>({ enabled: true, visible: true });
  useEffect(() => {
    let alive = true;
    getModuleStatus().then((v) => {
      if (alive) setStatus(v);
    });
    return () => {
      alive = false;
    };
  }, []);
  return status;
}
