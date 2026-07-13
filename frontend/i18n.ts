import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import HttpBackend from 'i18next-http-backend';

// Import types
import './types/i18n.d.ts';

const savedLang = localStorage.getItem('language') || 'zh';

i18n
  .use(HttpBackend)
  .use(initReactI18next)
  .init({
    // Lazy load from public folder. Locale files are static assets with no
    // content hash in their URL, so browsers/CDNs cache them across releases —
    // without a version param, newly added keys render as raw key paths on
    // prod until the cache expires (seen live: editor.cueHintEnter).
    backend: {
      // typeof-guard mirrors hooks/useVersionCheck.ts: vitest's transform of
      // some suites (ProjectsListView / InspirationPage.*) misses the vite
      // `define`, so a bare __APP_VERSION__ throws ReferenceError at import
      // time and fails those files in CI. Real builds always have the define,
      // so prod/dev behavior is unchanged.
      loadPath: `/locales/{{lng}}.json?v=${
        typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'
      }`,
    },
    lng: savedLang,
    fallbackLng: 'en',
    supportedLngs: ['en', 'zh'],

    // Enable debug in development
    debug: import.meta.env.DEV,

    interpolation: {
      escapeValue: false,
    },

    // React specific options
    react: {
      useSuspense: true,
    },
  });

export const changeLanguage = async (lang: 'en' | 'zh') => {
  await i18n.changeLanguage(lang);
  localStorage.setItem('language', lang);
};

export const getCurrentLanguage = () => i18n.language as 'en' | 'zh';

export const supportedLanguages = [
  { code: 'en' as const, label: 'English', short: 'EN' },
  { code: 'zh' as const, label: '中文', short: '中' },
];

export default i18n;
