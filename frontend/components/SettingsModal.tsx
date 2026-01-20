import React, { useState, useEffect } from 'react';
import { X, User, Users, Settings } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { PersonalSettings } from './PersonalSettings';
import { TeamSettings } from './TeamSettings';
import { InviteMembersModal } from './InviteMembersModal';

type SettingsTab = 'personal' | 'team';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialTab?: SettingsTab;
  currentTeamId: string | null;
  currentTeamName?: string;
  isTeamOwner?: boolean;
  user: {
    id: string;
    name: string;
    email: string;
    avatarUrl?: string;
    bio?: string;
  };
  onUserUpdated?: () => void;
  onTeamDeleted?: () => void;
  onTeamLeft?: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  initialTab = 'personal',
  currentTeamId,
  currentTeamName,
  isTeamOwner = false,
  user,
  onUserUpdated,
  onTeamDeleted,
  onTeamLeft,
}) => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab);
  const [isInviteModalOpen, setIsInviteModalOpen] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setActiveTab(initialTab);
    }
  }, [isOpen, initialTab]);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isInviteModalOpen) onClose();
    };
    if (isOpen) {
      document.addEventListener('keydown', handleEsc);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEsc);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose, isInviteModalOpen]);

  if (!isOpen) return null;

  const navItems = [
    { id: 'personal' as const, label: 'Personal Settings', icon: Settings, section: 'GENERAL SETTINGS' },
    ...(currentTeamId ? [{ id: 'team' as const, label: 'Team Settings', icon: Users, section: 'GENERAL SETTINGS' }] : []),
  ];

  const handleTeamDeleted = () => {
    onClose();
    onTeamDeleted?.();
  };

  const handleTeamLeft = () => {
    onClose();
    onTeamLeft?.();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-4xl mx-4 max-h-[85vh] flex overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Sidebar */}
        <div className="w-56 bg-zinc-950/50 border-r border-zinc-800 flex flex-col">
          {/* Sidebar Header */}
          <div className="p-4 border-b border-zinc-800">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center overflow-hidden">
                {user.avatarUrl ? (
                  <img src={user.avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
                ) : (
                  <span className="text-sm font-bold text-white">
                    {user.name.charAt(0).toUpperCase()}
                  </span>
                )}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-white truncate">{user.name}</p>
                <p className="text-xs text-zinc-500 truncate">{user.email}</p>
              </div>
            </div>
          </div>

          {/* Navigation */}
          <nav className="flex-1 p-3 space-y-4 overflow-y-auto">
            <div>
              <p className="px-3 text-[10px] font-semibold text-zinc-600 uppercase tracking-wider mb-2">
                General Settings
              </p>
              <div className="space-y-0.5">
                {navItems.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setActiveTab(item.id)}
                    className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                      activeTab === item.id
                        ? 'bg-zinc-800 text-white'
                        : 'text-zinc-400 hover:text-white hover:bg-zinc-800/50'
                    }`}
                  >
                    <item.icon size={16} />
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
          </nav>
        </div>

        {/* Content */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-800">
            <h2 className="text-lg font-semibold text-white">
              {activeTab === 'personal' ? 'Personal Settings' : (
                <>
                  Team Settings
                  {currentTeamName && (
                    <span className="text-zinc-500 font-normal ml-2">- {currentTeamName}</span>
                  )}
                </>
              )}
            </h2>
            <button
              onClick={onClose}
              className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
            >
              <X size={20} />
            </button>
          </div>

          {/* Content Area */}
          <div className="flex-1 overflow-y-auto p-6">
            {activeTab === 'personal' && (
              <div className="max-w-xl">
                <PersonalSettings user={user} onUserUpdated={onUserUpdated} />
              </div>
            )}

            {activeTab === 'team' && currentTeamId && (
              <TeamSettings
                teamId={currentTeamId}
                teamName={currentTeamName || ''}
                isOwner={isTeamOwner}
                currentUserId={user.id}
                currentUserName={user.name}
                currentUserEmail={user.email}
                onOpenInviteModal={() => setIsInviteModalOpen(true)}
                onTeamDeleted={handleTeamDeleted}
                onTeamLeft={handleTeamLeft}
              />
            )}
          </div>
        </div>
      </div>

      {/* Invite Modal */}
      {currentTeamId && (
        <InviteMembersModal
          isOpen={isInviteModalOpen}
          onClose={() => setIsInviteModalOpen(false)}
          teamId={currentTeamId}
          teamName={currentTeamName || ''}
        />
      )}
    </div>
  );
};
