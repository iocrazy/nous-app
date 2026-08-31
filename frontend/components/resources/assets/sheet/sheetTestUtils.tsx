// frontend/components/resources/assets/sheet/sheetTestUtils.tsx
//
// Shared scaffolding for the entity-sheet tests. NOT a test file itself.
//
// `translate` is a generic i18next stand-in rather than a hardcoded bundle:
// the sheet addresses ~90 keys, and a bundle would have to be kept in step
// with every one of them or tests would start asserting against raw key
// strings. It resolves `defaultValue` (or a positional fallback) and performs
// `{{name}}` interpolation, which is exactly the part the components depend
// on. The keys THEMSELVES are pinned by `i18nParity.test.ts`, against the real
// locale files - a different question, asked in the right place.

import React from 'react';

export function translate(key: string, opts?: string | Record<string, unknown>): string {
  const fallback = typeof opts === 'string' ? opts : (opts?.defaultValue as string | undefined);
  let out = fallback ?? key;
  if (opts && typeof opts === 'object') {
    for (const [name, value] of Object.entries(opts)) {
      if (name === 'defaultValue') continue;
      out = out.split(`{{${name}}}`).join(String(value));
    }
  }
  return out;
}

export const i18nMock = {
  useTranslation: () => ({
    t: (k: string, o?: string | Record<string, unknown>) => translate(k, o),
  }),
};

/** The scope every fixture belongs to (matches the wire fixture's team id). */
export const SCOPE_ID = '727145299382534200';
export const TEAM_ID = '42';

export function resPath(path: string): string {
  return `/team/${TEAM_ID}${path}`;
}

/**
 * A minimal `UiModal` stand-in for the tests that render `LinkAssetDialog`.
 * The real one is a portal with focus management; the dialog's behaviour under
 * test is which rows it offers and what it calls, not the shell around it.
 */
export const UiModalStub: React.FC<{
  isOpen: boolean;
  title: string;
  children: React.ReactNode;
}> = ({ isOpen, title, children }) =>
  isOpen ? (
    <div role="dialog" aria-label={title}>
      {children}
    </div>
  ) : null;
