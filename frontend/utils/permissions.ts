import { Permission } from '../types';

// Role → permissions mapping
const ROLE_PERMISSIONS: Record<string, string[]> = {
  owner: ['*'],
  admin: ['project.*', 'resource.*', 'member.*', 'review.*'],
  member: ['project.view', 'resource.view', 'review.view'],
};

/**
 * Resolve a team member role to a list of permission strings.
 */
export function resolvePermissions(role: string | null): string[] {
  if (!role) return [];
  return ROLE_PERMISSIONS[role] || [];
}

/**
 * Check if a list of permission strings satisfies a required permission.
 * Supports wildcards: '*' matches everything, 'project.*' matches 'project.view', etc.
 */
export function hasPermission(perms: string[], required: Permission): boolean {
  return perms.some(p =>
    p === '*' ||
    p === required ||
    (p.endsWith('.*') && required.startsWith(p.slice(0, -1)))
  );
}
