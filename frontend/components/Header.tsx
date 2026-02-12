import React from 'react';
import { Bell, User } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from './LanguageSwitcher';
import { PointsBadge } from './PointsBadge';
import { QuotaBar } from './QuotaBar';

interface HeaderProps {
  userProfile: {
    name: string;
    email: string;
    avatarUrl?: string;
  };
  unreadCount: number;
  onNotificationClick: () => void;
  onUserClick: () => void;
  onPointsClick: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  userProfile,
  unreadCount,
  onNotificationClick,
  onUserClick,
  onPointsClick,
}) => {
  const { t } = useTranslation();

  return (
    <header className="hidden md:flex h-16 items-center justify-end gap-4 px-6 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm fixed top-0 right-0 left-64 z-30">
      {/* Language Switcher */}
      <LanguageSwitcher />

      {/* Points Badge */}
      <PointsBadge onClick={onPointsClick} />

      {/* Storage Usage Bar */}
      <QuotaBar />


      {/* Notification Bell */}
      <button
        onClick={onNotificationClick}
        className="relative p-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800/50 transition-colors"
      >
        <Bell size={20} />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] bg-red-500 text-white text-xs font-bold rounded-full flex items-center justify-center px-1">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {/* User Avatar */}
      <button
        onClick={onUserClick}
        className="flex items-center gap-3 pl-3 pr-1 py-1 rounded-full hover:bg-zinc-800/50 transition-colors"
      >
        <span className="text-sm text-zinc-300 font-medium hidden lg:block">
          {userProfile.name}
        </span>
        <div className="w-9 h-9 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center overflow-hidden border-2 border-zinc-700">
          {userProfile.avatarUrl ? (
            <img src={userProfile.avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
          ) : (
            <User size={18} className="text-white" />
          )}
        </div>
      </button>
    </header>
  );
};
