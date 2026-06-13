import React, { useState } from 'react';
import { X, Users } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { createTeam } from '../services/teamService';
import { Team } from '../types';

interface CreateTeamModalProps {
  isOpen: boolean;
  onClose: () => void;
  onTeamCreated: (team: Team) => void;
}

export const CreateTeamModal: React.FC<CreateTeamModalProps> = ({
  isOpen,
  onClose,
  onTeamCreated,
}) => {
  const { t } = useTranslation();
  const [teamName, setTeamName] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!teamName.trim()) {
      setError(t('user.teamNameRequired') || 'Team name is required');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const team = await createTeam(teamName.trim());
      onTeamCreated(team);
      setTeamName('');
      onClose();
    } catch (err: unknown) {
      console.error('Create team error:', err);
      const errorObj = err as { message?: string; code?: string; details?: string };
      const message = errorObj?.message || errorObj?.details || 'Failed to create team';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleClose = () => {
    setTeamName('');
    setError(null);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={handleClose}
      />

      {/* Modal */}
      <div className="relative bg-ink-900 border border-ink-800 rounded-2xl shadow-2xl w-full max-w-md mx-4 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-ink-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-500/20 rounded-lg">
              <Users size={20} className="text-indigo-400" />
            </div>
            <h2 className="text-lg font-semibold text-white">
              {t('user.createTeam') || 'Create Team'}
            </h2>
          </div>
          <button
            onClick={handleClose}
            className="p-2 text-ink-400 hover:text-white hover:bg-ink-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6">
          <div className="mb-4">
            <label className="block text-sm font-medium text-ink-300 mb-2">
              {t('user.teamName') || 'Team Name'}
            </label>
            <input
              type="text"
              value={teamName}
              onChange={(e) => setTeamName(e.target.value)}
              placeholder={t('user.teamNamePlaceholder') || 'Enter team name'}
              className="w-full px-4 py-3 bg-ink-800 border border-ink-700 rounded-xl text-white placeholder-ink-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all"
              autoFocus
            />
          </div>

          {error && (
            <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <div className="flex gap-3">
            <button
              type="button"
              onClick={handleClose}
              className="flex-1 px-4 py-3 text-ink-300 bg-ink-800 hover:bg-ink-700 rounded-xl font-medium transition-colors"
            >
              {t('common.cancel') || 'Cancel'}
            </button>
            <button
              type="submit"
              disabled={isLoading || !teamName.trim()}
              className="flex-1 px-4 py-3 text-white bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 disabled:cursor-not-allowed rounded-xl font-medium transition-colors"
            >
              {isLoading ? (t('common.creating') || 'Creating...') : (t('common.create') || 'Create')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
