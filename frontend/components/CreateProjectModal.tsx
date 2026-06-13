import React, { useState, useEffect } from 'react';
import { X, FolderPlus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { createProject } from '../services/projectsService';
import { fetchMyTeams } from '../services/teamService';
import { Project, Team } from '../types';

interface CreateProjectModalProps {
  isOpen: boolean;
  onClose: () => void;
  onProjectCreated: (project: Project) => void;
}

export const CreateProjectModal: React.FC<CreateProjectModalProps> = ({
  isOpen,
  onClose,
  onProjectCreated,
}) => {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const [projectGroup, setProjectGroup] = useState('');
  const [projectType, setProjectType] = useState<'personal' | 'internal' | 'external'>('personal');
  const [teamId, setTeamId] = useState<string>('');
  const [teams, setTeams] = useState<Team[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      loadTeams();
    }
  }, [isOpen]);

  const loadTeams = async () => {
    try {
      const data = await fetchMyTeams();
      setTeams(data);
    } catch (err) {
      console.error('Failed to load teams:', err);
    }
  };

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError('Project name is required');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const project = await createProject({
        name: name.trim(),
        description: description.trim() || undefined,
        project_type: projectType,
        team_id: teamId || undefined,
        project_group: projectGroup.trim() || undefined,
        announcement: announcement.trim() || undefined,
      });
      onProjectCreated(project);
      resetForm();
    } catch (err: unknown) {
      console.error('Create project error:', err);
      const errorObj = err as { message?: string };
      setError(errorObj?.message || 'Failed to create project');
    } finally {
      setIsLoading(false);
    }
  };

  const resetForm = () => {
    setName('');
    setDescription('');
    setAnnouncement('');
    setProjectGroup('');
    setProjectType('personal');
    setTeamId('');
    setError(null);
  };

  const handleClose = () => {
    resetForm();
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
              <FolderPlus size={20} className="text-indigo-400" />
            </div>
            <h2 className="text-lg font-semibold text-ink-50">
              {t('mediatrack.createProject')}
            </h2>
          </div>
          <button
            onClick={handleClose}
            className="p-2 text-ink-400 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {/* Name */}
          <div>
            <div className="flex justify-between mb-2">
              <label className="text-sm font-medium text-ink-300">
                {t('projects.create.name', 'Project Name')}
              </label>
              <span className={`text-xs ${name.length >= 30 ? 'text-red-400' : 'text-ink-600'}`}>
                {name.length}/30
              </span>
            </div>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value.slice(0, 30))}
              placeholder={t('projects.create.namePlaceholder', 'Enter project name')}
              className="w-full px-4 py-3 bg-ink-800 border border-ink-700 rounded-xl text-ink-50 placeholder-ink-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all"
              autoFocus
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium text-ink-300 mb-2">
              {t('mediatrack.projectDescription', 'Description')}
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t('projects.create.descriptionPlaceholder', 'Optional description')}
              rows={2}
              className="w-full px-4 py-3 bg-ink-800 border border-ink-700 rounded-xl text-ink-50 placeholder-ink-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all resize-none"
            />
          </div>

          {/* Announcement */}
          <div>
            <div className="flex justify-between mb-2">
              <label className="text-sm font-medium text-ink-300">
                {t('projects.create.announcement', 'Announcement')}
              </label>
              <span className={`text-xs ${announcement.length >= 100 ? 'text-red-400' : 'text-ink-600'}`}>
                {announcement.length}/100
              </span>
            </div>
            <textarea
              value={announcement}
              onChange={(e) => setAnnouncement(e.target.value.slice(0, 100))}
              placeholder={t('projects.create.announcementPlaceholder', 'Help new members understand this project')}
              rows={2}
              className="w-full px-4 py-3 bg-ink-800 border border-ink-700 rounded-xl text-ink-50 placeholder-ink-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all resize-none"
            />
          </div>

          {/* Project Type + Group (side by side) */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-ink-300 mb-2">
                {t('mediatrack.projectType', 'Type')}
              </label>
              <select
                value={projectType}
                onChange={(e) => setProjectType(e.target.value as 'personal' | 'internal' | 'external')}
                className="w-full px-4 py-3 bg-ink-800 border border-ink-700 rounded-xl text-ink-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all appearance-none cursor-pointer"
              >
                <option value="personal">{t('mediatrack.personal')}</option>
                <option value="internal">{t('mediatrack.internal')}</option>
                <option value="external">{t('mediatrack.external')}</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-ink-300 mb-2">
                {t('projects.create.group', 'Group')}
              </label>
              <input
                type="text"
                value={projectGroup}
                onChange={(e) => setProjectGroup(e.target.value)}
                placeholder={t('projects.create.noGroup', 'No group')}
                className="w-full px-4 py-3 bg-ink-800 border border-ink-700 rounded-xl text-ink-50 placeholder-ink-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all"
              />
            </div>
          </div>

          {/* Team */}
          <div>
            <label className="block text-sm font-medium text-ink-300 mb-2">
              {t('mediatrack.selectTeam')}
            </label>
            <select
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              className="w-full px-4 py-3 bg-ink-800 border border-ink-700 rounded-xl text-ink-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all appearance-none cursor-pointer"
            >
              <option value="">{t('mediatrack.personal')}</option>
              {teams.map(team => (
                <option key={team.id} value={team.id}>{team.name}</option>
              ))}
            </select>
          </div>

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={handleClose}
              className="flex-1 px-4 py-3 text-ink-300 bg-ink-800 hover:bg-ink-700 rounded-xl font-medium transition-colors"
            >
              {t('common.cancel')}
            </button>
            <button
              type="submit"
              disabled={isLoading || !name.trim()}
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
