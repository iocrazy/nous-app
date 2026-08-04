import type { ReactNode } from 'react';
import { useModuleStatus } from '../hooks/useModuleStatus';
import { ModuleDisabledPage } from './ModuleDisabledPage';

/**
 * ADMIN-GLOBAL module guard (Module Control Center `visible` switch) — not to
 * be confused with the TEAM-level ./ModuleGuard (per-team view toggles).
 * Waits for the first status read so fail-closed modules never flash a bogus
 * disabled page; then either renders the route or the disabled notice.
 */
export function GlobalModuleGuard({ id, children }: { id: string; children: ReactNode }) {
  const { visible, loading } = useModuleStatus(id);
  if (loading) return null;
  if (!visible) return <ModuleDisabledPage />;
  return <>{children}</>;
}
