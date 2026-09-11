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
        // The service worker is deliberately thin: it never serves the app
        // itself, it only keeps an offline page and a few icons around.
        //
        // Why: a SW that precaches the build and answers navigations from
        // that precache pins the device to whatever build it installed. The
        // replacement SW has to download the whole build before it can
        // activate, and short-lived contexts (iOS Shortcuts' embedded web
        // view closes seconds after opening, a tab closed right after launch)
        // never let it finish — so the old shell kept being served on every
        // navigation. Cloudflare Pages already serves the
        // HTML network-fresh (max-age=0, must-revalidate) and the hashed
        // /assets/ files as immutable, so the browser HTTP cache handles
        // speed and every navigation loads the build that is live right now.
        //
        // ``autoUpdate`` + skipWaiting + clientsClaim: with a precache of a
        // handful of small files a new SW installs and takes over within
        // seconds of being fetched, with no prompt.
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
          // Precache ONLY small static files that don't change per build.
          // Never index.html (a cached shell is exactly what pins old builds)
          // and never /assets/** JS/CSS (that is what made each update
          // download ~230 files before it could activate). offline.html is
          // self-contained (inline CSS/SVG) and backs the navigation fallback
          // below.
          globPatterns: [
            'offline.html',
            'favicon.svg',
            'apple-touch-icon-180x180.png',
            'pwa-*.png',
            'icons/*.svg',
          ],
          // version.json must always be network-fresh — it's how a running
          // tab learns a newer build is live. Never precache it.
          globIgnores: ['**/version.json'],
          // On activation the precache drops every entry that is not in the
          // new manifest (that is how devices on the old SW shed the big
          // precache), and cleanupOutdatedCaches also deletes precaches left
          // behind by older Workbox versions.
          cleanupOutdatedCaches: true,
          // Activate the new SW immediately, then take over already-open
          // tabs. Without these, the user has to close every tab before
          // the new SW kicks in.
          skipWaiting: true,
          clientsClaim: true,
          // Must be an explicit null, not omitted: vite-plugin-pwa defaults
          // it to 'index.html', which registers a cached-shell navigation
          // route ahead of every runtimeCaching rule — and, with index.html
          // no longer precached, makes the SW throw on startup. Navigations
          // are handled by the first runtime rule below instead.
          navigateFallback: null,
          runtimeCaching: [
            // Page navigations (including /shortcuts/*) always go to the
            // network. The only thing the SW adds is a friendly offline page
            // instead of the browser's error when the network is down.
            // Keep this rule first so no later pattern can claim a navigation.
            {
              urlPattern: ({ request }) => request.mode === 'navigate',
              handler: 'NetworkOnly',
              options: { precacheFallback: { fallbackURL: '/offline.html' } },
            },
            // NOTE: do NOT add a caching rule for /assets/. Those files are
            // content-hashed and immutable on Cloudflare Pages, so the HTTP
            // cache already serves them instantly; a SW cache on top only
            // adds a way to run stale code.
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
