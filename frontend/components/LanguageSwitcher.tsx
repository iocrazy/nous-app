import React, { useState, useRef, useEffect } from 'react';
import { Globe, ChevronDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { changeLanguage, getCurrentLanguage } from '../i18n';

interface LanguageSwitcherProps {
  variant?: 'dropdown' | 'inline';
}

export const LanguageSwitcher: React.FC<LanguageSwitcherProps> = ({ variant = 'dropdown' }) => {
  const { i18n } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const languages = [
    { code: 'en', label: 'English', short: 'EN' },
    { code: 'zh', label: '中文', short: '中' },
  ];

  const currentLang = languages.find(l => l.code === i18n.language) || languages[0];

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleChange = (code: 'en' | 'zh') => {
    changeLanguage(code);
    setIsOpen(false);
  };

  if (variant === 'inline') {
    return (
      <div className="flex items-center gap-2">
        {languages.map(lang => (
          <button
            key={lang.code}
            onClick={() => handleChange(lang.code as 'en' | 'zh')}
            className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
              i18n.language === lang.code
                ? 'bg-indigo-600 text-white'
                : 'text-zinc-400 hover:text-white hover:bg-zinc-800'
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
        className="flex items-center gap-2 px-3 py-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800/50 transition-colors"
      >
        <Globe size={18} />
        <span className="text-sm font-medium">{currentLang.short}</span>
        <ChevronDown size={14} className={`transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full right-0 mt-2 bg-zinc-900 border border-zinc-800 rounded-xl shadow-xl py-1 min-w-[120px] z-50 animate-in fade-in slide-in-from-top-2 duration-200">
          {languages.map(lang => (
            <button
              key={lang.code}
              onClick={() => handleChange(lang.code as 'en' | 'zh')}
              className={`w-full px-4 py-2 text-left text-sm transition-colors ${
                i18n.language === lang.code
                  ? 'text-indigo-400 bg-indigo-500/10'
                  : 'text-zinc-300 hover:bg-zinc-800'
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
