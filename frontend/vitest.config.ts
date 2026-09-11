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
      // vite-plugin-pwa's virtual module only exists under vite.config.ts.
      'virtual:pwa-register': path.resolve(__dirname, './tests/stubs/virtual-pwa-register.ts'),
    },
  },
});
