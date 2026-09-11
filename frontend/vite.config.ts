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
      allowedHosts: ['cn.nous.ink', 'cn-sb.nous.ink', 'api.nous.ink', 'app.nous.ink', '10.0.0.3'],
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
      // Emit dist/version.json so a running tab can compare the live build's
      // version against its own baked-in __APP_VERSION__ (focus-triggered
      // check). Single source of truth = package.json. commitSha lets CI
      // verify the prod deploy carries this exact build.
      {
        name: 'emit-version-json',
        generateBundle() {
          this.emitFile({
            type: 'asset',
            fileName: 'version.json',
            source: JSON.stringify({
              version: pkg.version,
              // CF_PAGES_COMMIT_SHA on Cloudflare Pages (current host),
              // GITHUB_SHA for CI / local `wrangler pages deploy` builds.
              // Leaving only the Vercel var here silently emits an empty
              // commitSha, which makes the deploy-verify workflow poll
              // forever for a SHA that can never appear.
              commitSha: (
                env.CF_PAGES_COMMIT_SHA ||
                env.GITHUB_SHA ||
                ''
              ).slice(0, 7),
              buildTime: new Date().toISOString(),
            }),
          });
        },
      },
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
          name: 'Nous',
          short_name: 'Nous',
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
          // version.json must always be network-fresh — it's how a running
          // tab learns a newer build is live. Never precache it.
          globIgnores: ['**/version.json'],
          // Discard caches from previous SW versions on activation so the
          // new manifest doesn't try to reuse stale 404'd chunk URLs.
          cleanupOutdatedCaches: true,
          // Activate the new SW immediately, then take over already-open
          // tabs. Without these, the user has to close every tab before
          // the new SW kicks in.
          skipWaiting: true,
          clientsClaim: true,
          navigateFallback: '/index.html',
          // /shortcuts/ opens in iOS Shortcuts' embedded web view, which closes before a new SW can install — never serve it the cached shell.
          navigateFallbackDenylist: [/^\/api\//, /^\/media\//, /^\/stream\//, /^\/shortcuts\//],
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
            // 只保留真正首屏必需的两个：react 运行时 + supabase(鉴权)。
            //
            // ⚠️ 不要把 tiptap / recharts / @xyflow(reactflow) 列进 manualChunks。
            // 手动固化成独立 chunk 后，Vite 会把它们写进 index.html 的
            // modulepreload，等于给这些重型库开首屏优先通道 —— 实测三者合计
            // 295KB gzip 全部压在关键路径上，而它们只在编辑器/图表/画布页面用得到。
            // 交给 Rollup 按动态 import 边界自动切，它们才会跟着 lazy 组件走。
            'vendor-react': ['react', 'react-dom', 'react-router-dom'],
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
