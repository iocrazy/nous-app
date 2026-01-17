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
    // Lazy load from public folder
    backend: {
      loadPath: '/locales/{{lng}}.json',
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
