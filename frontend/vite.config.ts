import path from 'path';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { VitePWA } from 'vite-plugin-pwa';
import pkg from './package.json' with { type: 'json' };

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  return {
    server: {
      port: 3000,
      host: '0.0.0.0',
      allowedHosts: ['mediahubserver.heygo.cn', 'mediahubapi.heygo.cn', 'test.heygo.cn', '10.0.0.3'],
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8081',
          changeOrigin: true,
        },
        '/media': {
          target: 'http://127.0.0.1:8081',
          changeOrigin: true,
        },
        '/stream': {
          target: 'http://127.0.0.1:8081',
          changeOrigin: true,
        },
      },
    },
    plugins: [
      tailwindcss(),
      react(),
      VitePWA({
        // ``autoUpdate`` instead of ``prompt`` — the prompt path leaves the
        // new SW in a waiting state until the user clicks an "update"
        // button we never wired up, so SW caches drift forever and old
        // precache manifests reference chunks (e.g. ``minus-Du63Q9Ce.js``)
        // that no longer exist after a redeploy → 404 + bad-precaching-
        // response in console. autoUpdate + skipWaiting + clientsClaim
        // makes a fresh SW take over on the next navigation.
        registerType: 'autoUpdate',
        includeAssets: ['favicon.svg', 'apple-touch-icon-180x180.png'],
        manifest: {
          name: 'MediaHub',
          short_name: 'MediaHub',
          description: 'Media library and content management',
          theme_color: '#6366f1',
          background_color: '#000000',
          display: 'standalone',
          scope: '/',
          start_url: '/',
          icons: [
            { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
            { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
            { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
          ],
        },
        workbox: {
          maximumFileSizeToCacheInBytes: 3 * 1024 * 1024, // 3 MB
          // Discard caches from previous SW versions on activation so the
          // new manifest doesn't try to reuse stale 404'd chunk URLs.
          cleanupOutdatedCaches: true,
          // Activate the new SW immediately, then take over already-open
          // tabs. Without these, the user has to close every tab before
          // the new SW kicks in.
          skipWaiting: true,
          clientsClaim: true,
          navigateFallback: '/index.html',
          navigateFallbackDenylist: [/^\/api\//, /^\/media\//, /^\/stream\//],
          runtimeCaching: [
            // NOTE: do NOT add a CacheFirst rule for /assets/. Vite emits
            // content-hashed bundles (index-<hash>.js) that VitePWA already
            // precaches via the manifest, which is regenerated every build so
            // a new deploy ships new hashes + a fresh manifest. A CacheFirst
            // runtimeCaching rule on /assets/ overrides that: it serves the
            // OLD cached bundle and never fetches the new hash, so even after
            // the SW updates (skipWaiting/clientsClaim) the app keeps running
            // stale code until the user manually clears the SW. This was the
            // cause of users being stuck on old builds (e.g. the dropped
            // teams.is_personal query 400ing long after the fix shipped).
            {
              urlPattern: /^https:\/\/fonts\.googleapis\.com\/.*/i,
              handler: 'StaleWhileRevalidate',
              options: {
                cacheName: 'google-fonts-css',
                expiration: { maxEntries: 10, maxAgeSeconds: 60 * 60 * 24 * 365 },
              },
            },
            {
              urlPattern: /^https:\/\/fonts\.gstatic\.com\/.*/i,
              handler: 'CacheFirst',
              options: {
                cacheName: 'google-fonts-woff2',
                expiration: { maxEntries: 30, maxAgeSeconds: 60 * 60 * 24 * 365 },
              },
            },
            {
              urlPattern: /\/locales\/.+\.json$/,
              handler: 'StaleWhileRevalidate',
              options: {
                cacheName: 'i18n-locales',
                expiration: { maxEntries: 20, maxAgeSeconds: 60 * 60 * 24 * 7 },
              },
            },
          ],
        },
      }),
    ],
    define: {
      'process.env.API_KEY': JSON.stringify(env.GEMINI_API_KEY),
      'process.env.GEMINI_API_KEY': JSON.stringify(env.GEMINI_API_KEY),
      '__APP_VERSION__': JSON.stringify(pkg.version),
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            'vendor-react': ['react', 'react-dom', 'react-router-dom'],
            'vendor-tiptap': ['@tiptap/react', '@tiptap/starter-kit', '@tiptap/extension-placeholder'],
            'vendor-reactflow': ['@xyflow/react'],
            'vendor-charts': ['recharts'],
            'vendor-supabase': ['@supabase/supabase-js'],
          },
        },
      },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      }
    }
  };
});
