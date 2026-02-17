// frontend/hooks/usePermission.ts

/**
 * Hook for fetching and using effective permissions.
 *
 * Queries the backend ReBAC system to resolve the current user's
 * effective role on a specific object (library, folder, resource).
 */

import { useState, useEffect, useCallback } from 'react';
import { fetchEffectiveRole, EffectivePermission } from '../services/permissionService';

interface UsePermissionResult extends EffectivePermission {
  loading: boolean;
  canDo: (action: string) => boolean;
}

export function usePermission(
  objectType: string | null,
  objectId: string | null,
  teamId: string | null,
): UsePermissionResult {
  const [role, setRole] = useState<string>('viewer');
  const [capabilities, setCapabilities] = useState<string[]>(['view', 'download']);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!objectType || !objectId || !teamId) {
      // Personal scope or no object selected — grant full access
      setRole('admin');
      setCapabilities(['view', 'download', 'upload', 'update', 'copy', 'move', 'delete', 'share', 'manage']);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);

    fetchEffectiveRole(objectType, objectId, teamId)
      .then((result) => {
        if (!cancelled) {
          setRole(result.role || 'viewer');
          setCapabilities(result.capabilities || ['view', 'download']);
        }
      })
      .catch(() => {
        if (!cancelled) {
          // On error, default to viewer for safety
          setRole('viewer');
          setCapabilities(['view', 'download']);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [objectType, objectId, teamId]);

  const canDo = useCallback(
    (action: string) => Array.isArray(capabilities) && capabilities.includes(action),
    [capabilities],
  );

  return { role, capabilities, loading, canDo };
}
