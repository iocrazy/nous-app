import React, { useState } from 'react';
import { X, Loader2, Users, Lock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Team } from '../types';

interface CreateCollectionModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (name: string, teamId: string | null) => Promise<void>;
  teams: Team[];
}

export const CreateCollectionModal: React.FC<CreateCollectionModalProps> = ({
  isOpen,
  onClose,
  onSubmit,
  teams,
}) => {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    setIsSubmitting(true);
    setError(null);
    try {
      await onSubmit(name.trim(), selectedTeamId);
      setName('');
      setSelectedTeamId(null);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to create collection');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-md mx-4 shadow-2xl animate-in zoom-in-95 fade-in duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-zinc-800">
          <h2 className="text-lg font-semibold text-white">{t('collections.create')}</h2>
          <button
            onClick={onClose}
            className="p-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          {/* Collection Name */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-zinc-400">{t('collections.name')}</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('collections.namePlaceholder') || 'Enter collection name'}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2.5 text-white focus:border-indigo-500 outline-none transition-colors"
              autoFocus
            />
          </div>

          {/* Share with Team */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-zinc-400">{t('collections.shareWith')}</label>
            <div className="space-y-2">
              <button
                type="button"
                onClick={() => setSelectedTeamId(null)}
                className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg border transition-colors ${
                  selectedTeamId === null
                    ? 'bg-indigo-500/10 border-indigo-500/50 text-indigo-400'
                    : 'border-zinc-700 text-zinc-400 hover:bg-zinc-800'
                }`}
              >
                <Lock size={18} />
                <span>{t('collections.private')}</span>
              </button>

              {teams.map(team => (
                <button
                  key={team.id}
                  type="button"
                  onClick={() => setSelectedTeamId(team.id)}
                  className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg border transition-colors ${
                    selectedTeamId === team.id
                      ? 'bg-indigo-500/10 border-indigo-500/50 text-indigo-400'
                      : 'border-zinc-700 text-zinc-400 hover:bg-zinc-800'
                  }`}
                >
                  <Users size={18} />
                  <span>{team.name}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Submit Button */}
          <button
            type="submit"
            disabled={!name.trim() || isSubmitting}
            className="w-full py-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg font-medium transition-colors flex items-center justify-center gap-2"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="animate-spin" size={18} />
                {t('common.creating')}
              </>
            ) : (
              t('common.create')
            )}
          </button>
        </form>
      </div>
    </div>
  );
};
