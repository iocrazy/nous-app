import React, { useState, useRef, useEffect } from 'react';
import { Globe, ChevronDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { changeLanguage, supportedLanguages } from '../i18n';

interface LanguageSwitcherProps {
  variant?: 'dropdown' | 'inline';
}

export const LanguageSwitcher: React.FC<LanguageSwitcherProps> = ({ variant = 'dropdown' }) => {
  const { i18n } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [isChanging, setIsChanging] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const currentLang = supportedLanguages.find(l => l.code === i18n.language) || supportedLanguages[0];

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleChange = async (code: 'en' | 'zh') => {
    if (isChanging || code === i18n.language) {
      setIsOpen(false);
      return;
    }

    setIsChanging(true);
    try {
      await changeLanguage(code);
    } finally {
      setIsChanging(false);
      setIsOpen(false);
    }
  };

  if (variant === 'inline') {
    return (
      <div className="flex items-center gap-2">
        {supportedLanguages.map(lang => (
          <button
            key={lang.code}
            onClick={() => handleChange(lang.code)}
            disabled={isChanging}
            className={`px-3 py-1.5 text-sm rounded-lg transition-colors disabled:opacity-50 ${
              i18n.language === lang.code
                ? 'bg-indigo-600 text-white'
                : 'text-ink-400 hover:text-ink-50 hover:bg-ink-800'
            }`}
          >
            {lang.label}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={isChanging}
        className="flex items-center gap-2 px-3 py-2 rounded-lg text-ink-400 hover:text-ink-50 hover:bg-ink-800/50 transition-colors disabled:opacity-50"
      >
        <Globe size={18} />
        <span className="text-sm font-medium">{currentLang.short}</span>
        <ChevronDown size={14} className={`transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full right-0 mt-2 bg-ink-900 border border-ink-800 rounded-xl shadow-xl py-1 min-w-[120px] z-50 animate-in fade-in slide-in-from-top-2 duration-200">
          {supportedLanguages.map(lang => (
            <button
              key={lang.code}
              onClick={() => handleChange(lang.code)}
              disabled={isChanging}
              className={`w-full px-4 py-2 text-left text-sm transition-colors disabled:opacity-50 ${
                i18n.language === lang.code
                  ? 'text-indigo-400 bg-indigo-500/10'
                  : 'text-ink-300 hover:bg-ink-800'
              }`}
            >
              {lang.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};
