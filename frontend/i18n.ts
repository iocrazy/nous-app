import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './locales/en.json';
import zh from './locales/zh.json';

const savedLang = localStorage.getItem('language') || 'zh';

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
  },
  lng: savedLang,
  fallbackLng: 'en',
  interpolation: {
    escapeValue: false,
  },
});

export const changeLanguage = (lang: 'en' | 'zh') => {
  i18n.changeLanguage(lang);
  localStorage.setItem('language', lang);
};

export const getCurrentLanguage = () => i18n.language as 'en' | 'zh';

export default i18n;
