import React, { useState, useEffect } from 'react';
import { X, FolderPlus, GitBranch, Target } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { createProject } from '../services/projectsService';
import { fetchMyTeams } from '../services/teamService';
import { fetchTemplates } from '../services/workflowService';
import { Project, Team, WorkflowTemplate } from '../types';
import { UiSelect } from './ui';

type WorkflowMethod = 'live' | 'ai' | 'hybrid';
const WORKFLOW_METHODS: WorkflowMethod[] = ['live', 'ai', 'hybrid'];
/** Sentinel for the "No workflow" card (distinct from a real template id). */
const NO_WORKFLOW = '';

interface CreateProjectModalProps {
  isOpen: boolean;
  onClose: () => void;
  onProjectCreated: (project: Project) => void;
  /**
   * Snowflake id of the collaborative team the modal should default the Team
   * selector to (creating a project inside Team X's workspace pre-selects
   * Team X). Empty string / undefined = personal workspace → defaults to
   * "Personal" (a personal project is stored with team_id=NULL). Ids stay
   * strings (Snowflake-safe).
   */
  defaultTeamId?: string;
  /**
   * Ideation (M1.5): when the modal is opened from a topic, its id is stamped
   * onto the new project (projects.topic_id) and the title pre-fills the name.
   */
  topicId?: string;
  topicTitle?: string;
}

export const CreateProjectModal: React.FC<CreateProjectModalProps> = ({
  isOpen,
  onClose,
  onProjectCreated,
  defaultTeamId = '',
  topicId,
  topicTitle,
}) => {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const [projectGroup, setProjectGroup] = useState('');
  const [projectType, setProjectType] = useState<'personal' | 'internal' | 'external'>('personal');
  const [teamId, setTeamId] = useState<string>(defaultTeamId);
  const [teams, setTeams] = useState<Team[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Workflow block — only meaningful for a real team (templates are team-scoped;
  // a personal project has team_id=NULL and no templates to instantiate).
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [workflowTemplateId, setWorkflowTemplateId] = useState<string>(NO_WORKFLOW);
  const [workflowMethod, setWorkflowMethod] = useState<WorkflowMethod>('hybrid');

  useEffect(() => {
    if (isOpen) {
      loadTeams();
      // Re-sync the pre-selected team each time the modal opens so switching
      // workspaces between opens picks up the new default.
      setTeamId(defaultTeamId);
      // Ideation: seed the name from the source topic's title (name is capped
      // at 30 chars, same as the input's slice).
      if (topicTitle) setName(topicTitle.slice(0, 30));
    }
  }, [isOpen, defaultTeamId, topicTitle]);

  // Load the selected team's workflow templates; default to its Short-form
  // (the seeded is_default). Personal workspace (no team) → no templates.
  useEffect(() => {
    if (!isOpen || !teamId) {
      setTemplates([]);
      setWorkflowTemplateId(NO_WORKFLOW);
      return;
    }
    let alive = true;
    fetchTemplates(teamId)
      .then((list) => {
        if (!alive) return;
        setTemplates(list);
        setWorkflowTemplateId(list.find((tpl) => tpl.is_default)?.id ?? NO_WORKFLOW);
      })
      .catch((err) => {
        console.error('Failed to load workflow templates:', err);
        if (alive) setTemplates([]);
      });
    return () => {
      alive = false;
    };
  }, [isOpen, teamId]);

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
        workflow_template_id: workflowTemplateId || null,
        workflow_method: workflowTemplateId ? workflowMethod : null,
        topic_id: topicId || undefined,
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
    setTeamId(defaultTeamId);
    setWorkflowTemplateId(NO_WORKFLOW);
    setWorkflowMethod('hybrid');
    setError(null);
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" data-testid="create-project-modal">
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
            <div className="p-2 bg-[var(--accent-soft)] rounded-lg">
              <FolderPlus size={20} className="text-[var(--accent-text)]" />
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
          {/* From-topic provenance chip (Ideation M1.5) */}
          {topicId && topicTitle && (
            <div
              data-testid="from-topic-chip"
              className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-[var(--accent-soft)] px-2.5 py-1 text-xs text-[var(--accent-text)]"
            >
              <Target size={12} className="shrink-0" />
              <span className="truncate">
                {t('projects.ideation.fromTopic', 'From topic')}: {topicTitle}
              </span>
            </div>
          )}

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
              data-testid="project-name-input"
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
              <UiSelect
                value={projectType}
                onChange={(e) => setProjectType(e.target.value as 'personal' | 'internal' | 'external')}
                className="w-full"
              >
                <option value="personal">{t('mediatrack.personal')}</option>
                <option value="internal">{t('mediatrack.internal')}</option>
                <option value="external">{t('mediatrack.external')}</option>
              </UiSelect>
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
            <UiSelect
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              className="w-full"
            >
              <option value="">{t('mediatrack.personal')}</option>
              {teams.map(team => (
                <option key={team.id} value={team.id}>{team.name}</option>
              ))}
            </UiSelect>
          </div>

          {/* Workflow — team projects only (templates are team-scoped). */}
          {teamId && templates.length > 0 && (
            <div data-testid="create-project-workflow">
              <label className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink-300">
                <GitBranch size={14} className="text-ink-500" />
                {t('projects.workflow.create.title')}
              </label>
              <div className="grid grid-cols-3 gap-2">
                {templates.map((tpl) => (
                  <WorkflowChoiceCard
                    key={tpl.id}
                    title={tpl.name}
                    hint={`${tpl.node_count} nodes`}
                    selected={workflowTemplateId === tpl.id}
                    onClick={() => setWorkflowTemplateId(tpl.id)}
                    testId={`workflow-template-${tpl.id}`}
                  />
                ))}
                <WorkflowChoiceCard
                  title={t('projects.workflow.create.noWorkflow')}
                  hint={t('projects.workflow.create.noWorkflowHint')}
                  selected={workflowTemplateId === NO_WORKFLOW}
                  onClick={() => setWorkflowTemplateId(NO_WORKFLOW)}
                  testId="workflow-template-none"
                />
              </div>

              {workflowTemplateId !== NO_WORKFLOW && (
                <div className="mt-3">
                  <label className="mb-1.5 block text-xs font-medium text-ink-400">
                    {t('projects.workflow.create.method')}
                  </label>
                  <div className="flex gap-2">
                    {WORKFLOW_METHODS.map((m) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => setWorkflowMethod(m)}
                        data-testid={`workflow-method-${m}`}
                        className={`flex-1 rounded-lg border px-3 py-2 text-sm transition ${
                          workflowMethod === m
                            ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]'
                            : 'border-ink-700 text-ink-400 hover:border-ink-600'
                        }`}
                      >
                        {t(
                          m === 'live'
                            ? 'projects.workflow.create.methodLive'
                            : m === 'ai'
                              ? 'projects.workflow.create.methodAI'
                              : 'projects.workflow.create.methodHybrid',
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

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

/** One selectable card in the create-project Workflow block. */
function WorkflowChoiceCard({
  title,
  hint,
  selected,
  onClick,
  testId,
}: {
  title: string;
  hint: string;
  selected: boolean;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className={`flex flex-col items-start gap-0.5 rounded-lg border px-3 py-2.5 text-left transition ${
        selected
          ? 'border-[var(--accent-border)] bg-[var(--accent-soft)]'
          : 'border-ink-700 hover:border-ink-600'
      }`}
    >
      <span className={`text-[13px] font-medium ${selected ? 'text-[var(--accent-text)]' : 'text-ink-100'}`}>
        {title}
      </span>
      <span className="text-[11px] text-ink-500">{hint}</span>
    </button>
  );
}
