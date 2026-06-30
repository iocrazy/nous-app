import { useEffect, useState } from 'react';
import { getModuleStatus } from '../services/topicService';

/**
 * Global Topic Inspiration master switch (admin-controlled). Defaults to `true`
 * while loading / on error so the feature is never hidden by a transient
 * failure. When an admin turns the module off, the nav item and page hide.
 */
export function useTopicModuleEnabled(): boolean {
  const [enabled, setEnabled] = useState(true);
  useEffect(() => {
    let alive = true;
    getModuleStatus().then((v) => {
      if (alive) setEnabled(v);
    });
    return () => {
      alive = false;
    };
  }, []);
  return enabled;
}
