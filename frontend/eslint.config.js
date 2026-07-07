// Surgical hooks gate. This config intentionally enables ONLY the two
// react-hooks rules — no recommended preset — so it blocks the specific
// class of bug that shipped to prod in PR #1094 (early-return-then-useState,
// React error #310) without drowning the codebase in pre-existing style
// noise. Broaden deliberately, one rule at a time, not by turning on presets.
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

// Stub for the `react` plugin namespace. We deliberately do NOT load
// eslint-plugin-react (this gate is hooks-only), but the codebase carries
// pre-existing `// eslint-disable-next-line react/no-danger` annotations on
// sanitized dangerouslySetInnerHTML. Without a definition for that rule,
// ESLint fails the run with a hard "Definition for rule not found" error —
// unrelated to hooks. This no-op keeps those directives resolvable (they
// degrade to a harmless "unused directive" warning, like the core-rule
// no-alert / no-console disables already do) instead of blocking CI.
const reactStub = {
  rules: {
    'no-danger': { create: () => ({}) },
  },
};

export default [
  {
    ignores: [
      'dist/**',
      'dist-ssr/**',
      'dev-dist/**',
      'node_modules/**',
      'playwright-report/**',
      'test-results/**',
      'blob-report/**',
      'coverage/**',
      'public/**',
      '.vercel/**',
    ],
  },
  {
    files: ['**/*.ts', '**/*.tsx'],
    languageOptions: {
      // Parser only — no `project` / type-aware linting, keeps lint fast.
      parser: tseslint.parser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      react: reactStub,
    },
    rules: {
      // Blocking: this is the #1094 class of bug.
      'react-hooks/rules-of-hooks': 'error',
      // Non-blocking signal only — warnings do not fail CI.
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
];
