import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Trash2, Loader2 } from 'lucide-react';
import { Project } from '../types';
import { updateProject, deleteProject } from '../services/projectsService';

interface ProjectSettingsPanelProps {
  project: Project;
  isOpen: boolean;
  onClose: () => void;
  onUpdated: (project: Project) => void;
  onDeleted: () => void;
}

export const ProjectSettingsPanel: React.FC<ProjectSettingsPanelProps> = ({
  project, isOpen, onClose, onUpdated, onDeleted
}) => {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);
  const [name, setName] = useState(project.name);
  const [announcement, setAnnouncement] = useState(project.announcement || '');
  const [projectType, setProjectType] = useState(project.project_type);
  const [projectGroup, setProjectGroup] = useState(project.project_group || '');
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  useEffect(() => {
    setName(project.name);
    setAnnouncement(project.announcement || '');
    setProjectType(project.project_type);
    setProjectGroup(project.project_group || '');
  }, [project]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    if (isOpen) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [isOpen, onClose]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  const handleSave = async () => {
    if (!name.trim()) return;
    setIsSaving(true);
    try {
      const updated = await updateProject(project.id, {
        name: name.trim(),
        announcement: announcement.trim() || null,
        project_type: projectType,
        project_group: projectGroup.trim() || null,
      } as any);
      onUpdated(updated);
    } catch (err) {
      console.error('Failed to update project:', err);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await deleteProject(project.id);
      onDeleted();
    } catch (err) {
      console.error('Failed to delete project:', err);
    } finally {
      setIsDeleting(false);
      setShowDeleteConfirm(false);
    }
  };

  const hasChanges =
    name !== project.name ||
    (announcement || '') !== (project.announcement || '') ||
    projectType !== project.project_type ||
    (projectGroup || '') !== (project.project_group || '');

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" />

      {/* Panel */}
      <div
        ref={panelRef}
        className="relative w-96 bg-ink-900 border-l border-ink-800 h-full overflow-y-auto animate-in slide-in-from-right duration-200"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-ink-800">
          <h2 className="text-lg font-semibold text-ink-50">
            {t('projects.settings.title', 'Project Settings')}
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 text-ink-400 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        <div className="p-5 space-y-6">
          {/* Project Name */}
          <div>
            <div className="flex justify-between mb-2">
              <label className="text-sm font-medium text-ink-300">
                {t('projects.settings.name', 'Project Name')}
              </label>
              <span className="text-xs text-ink-600">{name.length}/30</span>
            </div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value.slice(0, 30))}
              className="w-full px-3 py-2.5 bg-ink-800 border border-ink-700 rounded-lg text-sm text-ink-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          {/* Announcement */}
          <div>
            <div className="flex justify-between mb-2">
              <label className="text-sm font-medium text-ink-300">
                {t('projects.settings.announcement', 'Announcement')}
              </label>
              <span className="text-xs text-ink-600">{announcement.length}/100</span>
            </div>
            <textarea
              value={announcement}
              onChange={(e) => setAnnouncement(e.target.value.slice(0, 100))}
              placeholder={t('projects.settings.announcementPlaceholder', 'Help new members understand this project')}
              rows={3}
              className="w-full px-3 py-2.5 bg-ink-800 border border-ink-700 rounded-lg text-sm text-ink-200 placeholder-ink-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 resize-none"
            />
          </div>

          {/* Project Type */}
          <div>
            <label className="block text-sm font-medium text-ink-300 mb-2">
              {t('projects.settings.type', 'Project Type')}
            </label>
            <select
              value={projectType}
              onChange={(e) => setProjectType(e.target.value as any)}
              className="w-full px-3 py-2.5 bg-ink-800 border border-ink-700 rounded-lg text-sm text-ink-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value="internal">{t('projects.settings.typeInternal', 'Internal')}</option>
              <option value="external">{t('projects.settings.typeExternal', 'External')}</option>
              <option value="personal">{t('projects.settings.typePersonal', 'Personal')}</option>
            </select>
          </div>

          {/* Project Group */}
          <div>
            <label className="block text-sm font-medium text-ink-300 mb-2">
              {t('projects.settings.group', 'Project Group')}
            </label>
            <input
              value={projectGroup}
              onChange={(e) => setProjectGroup(e.target.value)}
              placeholder={t('projects.settings.noGroup', 'No group')}
              className="w-full px-3 py-2.5 bg-ink-800 border border-ink-700 rounded-lg text-sm text-ink-200 placeholder-ink-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          {/* Save */}
          <button
            onClick={handleSave}
            disabled={!hasChanges || isSaving || !name.trim()}
            className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-ink-700 disabled:text-ink-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2"
          >
            {isSaving && <Loader2 size={14} className="animate-spin" />}
            {t('projects.settings.save', 'Save Changes')}
          </button>

          {/* Danger Zone */}
          <div className="border-t border-ink-800 pt-6">
            <h3 className="text-sm font-medium text-red-400 mb-3">
              {t('projects.settings.dangerZone', 'Danger Zone')}
            </h3>
            {!showDeleteConfirm ? (
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className="flex items-center gap-2 px-4 py-2.5 border border-red-500/30 text-red-400 hover:bg-red-500/10 rounded-lg text-sm transition-colors"
              >
                <Trash2 size={14} />
                {t('projects.settings.deleteProject', 'Delete Project')}
              </button>
            ) : (
              <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 space-y-3">
                <p className="text-sm text-red-300">
                  {t('projects.deleteConfirm.message', 'Deleting "{{name}}" will permanently remove all files and comments. This cannot be undone.', { name: project.name })}
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={handleDelete}
                    disabled={isDeleting}
                    className="flex-1 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2"
                  >
                    {isDeleting && <Loader2 size={14} className="animate-spin" />}
                    {t('projects.deleteConfirm.confirm', 'Delete')}
                  </button>
                  <button
                    onClick={() => setShowDeleteConfirm(false)}
                    className="flex-1 py-2 bg-ink-800 hover:bg-ink-700 text-ink-300 rounded-lg text-sm font-medium transition-colors"
                  >
                    {t('projects.deleteConfirm.cancel', 'Cancel')}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
