/**
 * Centralized API base URL accessor.
 * All frontend files should import from here instead of defining their own.
 */
export const getApiUrl = (): string => {
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    return (import.meta.env.VITE_API_URL || '').trim();
  }
  return 'http://localhost:8080';
};
