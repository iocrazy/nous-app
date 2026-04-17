import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['**/*.test.{ts,tsx}'],
    exclude: ['**/node_modules/**', '**/dist/**', 'e2e/**'],
    setupFiles: ['./tests/setup.ts'],
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './'),
    },
  },
});
