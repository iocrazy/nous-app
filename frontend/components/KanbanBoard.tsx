import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Loader2,
  Plus,
  GripVertical,
  Calendar,
  User,
  X,
  Trash2,
} from 'lucide-react';
import {
  ProjectTask,
  fetchProjectTasks,
  createProjectTask,
  updateProjectTask,
  deleteProjectTask,
} from '../services/projectTasksService';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface KanbanBoardProps {
  projectId: string;
  teamId?: string;
}

type ColumnStatus = 'todo' | 'in_progress' | 'done';

interface ColumnDef {
  id: ColumnStatus;
  /** Task statuses that belong to this column */
  statuses: ProjectTask['status'][];
  colorClass: string;
  dotColor: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const COLUMNS: ColumnDef[] = [
  {
    id: 'todo',
    statuses: ['todo', 'on_hold'],
    colorClass: 'text-zinc-400',
    dotColor: 'bg-zinc-500',
  },
  {
    id: 'in_progress',
    statuses: ['in_progress'],
    colorClass: 'text-blue-400',
    dotColor: 'bg-blue-500',
  },
  {
    id: 'done',
    statuses: ['done'],
    colorClass: 'text-emerald-400',
    dotColor: 'bg-emerald-500',
  },
];

const TASK_TYPE_COLORS: Record<ProjectTask['task_type'], string> = {
  general: 'bg-zinc-700 text-zinc-300',
  storyboard: 'bg-purple-900/60 text-purple-300',
  script: 'bg-blue-900/60 text-blue-300',
  filming: 'bg-orange-900/60 text-orange-300',
  editing: 'bg-emerald-900/60 text-emerald-300',
  review: 'bg-pink-900/60 text-pink-300',
};

const STATUS_FOR_COLUMN: Record<ColumnStatus, ProjectTask['status']> = {
  todo: 'todo',
  in_progress: 'in_progress',
  done: 'done',
};

// ---------------------------------------------------------------------------
// Helper: bucket tasks into columns
// ---------------------------------------------------------------------------

function bucketTasks(tasks: ProjectTask[]): Record<ColumnStatus, ProjectTask[]> {
  const buckets: Record<ColumnStatus, ProjectTask[]> = {
    todo: [],
    in_progress: [],
    done: [],
  };

  for (const task of tasks) {
    if (task.status === 'todo' || task.status === 'on_hold') {
      buckets.todo.push(task);
    } else if (task.status === 'in_progress') {
      buckets.in_progress.push(task);
    } else if (task.status === 'done' || task.status === 'cancelled') {
      buckets.done.push(task);
    } else {
      buckets.todo.push(task);
    }
  }

  // sort each bucket by sort_order
  for (const key of Object.keys(buckets) as ColumnStatus[]) {
    buckets[key].sort((a, b) => a.sort_order - b.sort_order);
  }

  return buckets;
}

// ---------------------------------------------------------------------------
// Helper: is date overdue
// ---------------------------------------------------------------------------

function isOverdue(dueDateStr: string | null): boolean {
  if (!dueDateStr) return false;
  const due = new Date(dueDateStr);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return due < today;
}

function formatDate(dueDateStr: string): string {
  const d = new Date(dueDateStr);
  const month = d.toLocaleString('default', { month: 'short' });
  return `${month} ${d.getDate()}`;
}

// ---------------------------------------------------------------------------
// TaskCard
// ---------------------------------------------------------------------------

interface TaskCardProps {
  task: ProjectTask;
  onUpdate: (taskId: string, data: Partial<ProjectTask>) => Promise<void>;
  onDelete: (taskId: string) => Promise<void>;
  onDragStart: (e: React.DragEvent, taskId: string) => void;
  t: (key: string) => string;
}

const TaskCard: React.FC<TaskCardProps> = ({ task, onUpdate, onDelete, onDragStart, t }) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(task.title);
  const [editDescription, setEditDescription] = useState(task.description || '');
  const [editDueDate, setEditDueDate] = useState(task.due_date || '');
  const [editTaskType, setEditTaskType] = useState(task.task_type);
  const titleInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing && titleInputRef.current) {
      titleInputRef.current.focus();
    }
  }, [isEditing]);

  const handleSave = async () => {
    if (!editTitle.trim()) return;
    await onUpdate(task.id, {
      title: editTitle.trim(),
      description: editDescription.trim() || null,
      due_date: editDueDate || null,
      task_type: editTaskType,
    });
    setIsEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSave();
    }
    if (e.key === 'Escape') {
      setEditTitle(task.title);
      setEditDescription(task.description || '');
      setEditDueDate(task.due_date || '');
      setEditTaskType(task.task_type);
      setIsEditing(false);
    }
  };

  const overdue = isOverdue(task.due_date) && task.status !== 'done';

  if (isEditing) {
    return (
      <div className="bg-zinc-900 border border-indigo-500/50 rounded-lg p-3 space-y-2">
        <input
          ref={titleInputRef}
          value={editTitle}
          onChange={(e) => setEditTitle(e.target.value)}
          onKeyDown={handleKeyDown}
          className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
          placeholder="Task title"
        />
        <textarea
          value={editDescription}
          onChange={(e) => setEditDescription(e.target.value)}
          onKeyDown={handleKeyDown}
          className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-indigo-500 resize-none"
          placeholder="Description (optional)"
          rows={2}
        />
        <div className="flex items-center gap-2">
          <select
            value={editTaskType}
            onChange={(e) => setEditTaskType(e.target.value as ProjectTask['task_type'])}
            className="bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-xs text-white focus:outline-none focus:border-indigo-500"
          >
            {(['general', 'storyboard', 'script', 'filming', 'editing', 'review'] as const).map(
              (type) => (
                <option key={type} value={type}>
                  {t(`kanban.taskTypes.${type}`)}
                </option>
              )
            )}
          </select>
          <input
            type="date"
            value={editDueDate}
            onChange={(e) => setEditDueDate(e.target.value)}
            className="bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-xs text-white focus:outline-none focus:border-indigo-500"
          />
        </div>
        <div className="flex items-center justify-between pt-1">
          <button
            onClick={() => onDelete(task.id)}
            className="text-xs text-red-400 hover:text-red-300 flex items-center gap-1 transition-colors"
          >
            <Trash2 size={12} />
            {t('common.delete')}
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setEditTitle(task.title);
                setEditDescription(task.description || '');
                setEditDueDate(task.due_date || '');
                setEditTaskType(task.task_type);
                setIsEditing(false);
              }}
              className="px-3 py-1 text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
            >
              {t('common.cancel')}
            </button>
            <button
              onClick={handleSave}
              className="px-3 py-1 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-colors"
            >
              {t('common.save')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, task.id)}
      onClick={() => setIsEditing(true)}
      className="bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-lg p-3 cursor-pointer group transition-all duration-150"
    >
      <div className="flex items-start gap-2">
        <div className="mt-0.5 text-zinc-600 opacity-0 group-hover:opacity-100 transition-opacity cursor-grab">
          <GripVertical size={14} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm text-zinc-200 leading-snug mb-2">{task.title}</p>

          <div className="flex items-center gap-2 flex-wrap">
            {/* Task type badge */}
            <span
              className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
                TASK_TYPE_COLORS[task.task_type]
              }`}
            >
              {t(`kanban.taskTypes.${task.task_type}`)}
            </span>

            {/* Due date */}
            {task.due_date && (
              <span
                className={`inline-flex items-center gap-1 text-[10px] ${
                  overdue ? 'text-red-400' : 'text-zinc-500'
                }`}
              >
                <Calendar size={10} />
                {formatDate(task.due_date)}
                {overdue && (
                  <span className="text-red-400 font-medium ml-0.5">
                    ({t('kanban.overdue')})
                  </span>
                )}
              </span>
            )}

            {/* Assignee */}
            {(task.assignee_id || task.assignee_email) && (
              <span className="inline-flex items-center gap-1 text-[10px] text-zinc-500">
                <User size={10} />
                {task.assignee_email
                  ? task.assignee_email.split('@')[0]
                  : task.assignee_id?.slice(0, 6)}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// KanbanColumn
// ---------------------------------------------------------------------------

interface KanbanColumnProps {
  column: ColumnDef;
  tasks: ProjectTask[];
  onCreateTask: (columnStatus: ProjectTask['status'], title: string) => Promise<void>;
  onUpdateTask: (taskId: string, data: Partial<ProjectTask>) => Promise<void>;
  onDeleteTask: (taskId: string) => Promise<void>;
  onDragStart: (e: React.DragEvent, taskId: string) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent, columnId: ColumnStatus) => void;
  isDragOver: boolean;
  t: (key: string) => string;
}

const KanbanColumn: React.FC<KanbanColumnProps> = ({
  column,
  tasks,
  onCreateTask,
  onUpdateTask,
  onDeleteTask,
  onDragStart,
  onDragOver,
  onDrop,
  isDragOver,
  t,
}) => {
  const [isAdding, setIsAdding] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isAdding && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isAdding]);

  const handleCreate = async () => {
    const title = newTitle.trim();
    if (!title) return;
    await onCreateTask(STATUS_FOR_COLUMN[column.id], title);
    setNewTitle('');
    // Keep add mode open for quick successive adds
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleCreate();
    }
    if (e.key === 'Escape') {
      setNewTitle('');
      setIsAdding(false);
    }
  };

  // Translate the column key
  const columnLabel =
    column.id === 'todo'
      ? t('kanban.todo')
      : column.id === 'in_progress'
      ? t('kanban.inProgress')
      : t('kanban.done');

  return (
    <div
      className={`flex flex-col min-w-[280px] max-w-[360px] flex-1 bg-zinc-900/50 border rounded-xl transition-colors duration-150 ${
        isDragOver ? 'border-indigo-500/50 bg-indigo-500/5' : 'border-zinc-800'
      }`}
      onDragOver={onDragOver}
      onDrop={(e) => onDrop(e, column.id)}
    >
      {/* Column header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${column.dotColor}`} />
          <span className={`text-sm font-semibold ${column.colorClass}`}>{columnLabel}</span>
          <span className="ml-1 text-xs text-zinc-600 bg-zinc-800 px-1.5 py-0.5 rounded-full">
            {tasks.length}
          </span>
        </div>
        <button
          onClick={() => setIsAdding(true)}
          className="p-1 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded transition-colors"
        >
          <Plus size={16} />
        </button>
      </div>

      {/* Task cards */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2 min-h-[120px]">
        {tasks.length === 0 && !isAdding && (
          <p className="text-center text-xs text-zinc-600 py-6">{t('kanban.noTasks')}</p>
        )}

        {tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            onUpdate={onUpdateTask}
            onDelete={onDeleteTask}
            onDragStart={onDragStart}
            t={t}
          />
        ))}

        {/* Inline new task input */}
        {isAdding && (
          <div className="mt-2">
            <input
              ref={inputRef}
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t('kanban.addTask')}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
            />
            <div className="flex items-center gap-2 mt-2">
              <button
                onClick={handleCreate}
                disabled={!newTitle.trim()}
                className="px-3 py-1 text-xs bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white rounded-lg transition-colors"
              >
                {t('common.create')}
              </button>
              <button
                onClick={() => {
                  setNewTitle('');
                  setIsAdding(false);
                }}
                className="p-1 text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                <X size={14} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Bottom add button (shown when not already adding) */}
      {!isAdding && (
        <button
          onClick={() => setIsAdding(true)}
          className="flex items-center gap-2 mx-3 mb-3 px-3 py-2 text-xs text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/60 rounded-lg transition-colors"
        >
          <Plus size={14} />
          {t('kanban.addTask')}
        </button>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// KanbanBoard (main export)
// ---------------------------------------------------------------------------

export const KanbanBoard: React.FC<KanbanBoardProps> = ({ projectId }) => {
  const { t } = useTranslation();

  const [tasks, setTasks] = useState<ProjectTask[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dragOverColumn, setDragOverColumn] = useState<ColumnStatus | null>(null);
  const draggedTaskId = useRef<string | null>(null);

  // Load tasks
  const loadTasks = useCallback(async () => {
    try {
      const data = await fetchProjectTasks(projectId);
      setTasks(data);
      setError(null);
    } catch (err) {
      console.error('Failed to load tasks:', err);
      setError('Failed to load tasks');
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    setIsLoading(true);
    loadTasks();
  }, [loadTasks]);

  // Create task
  const handleCreateTask = async (status: ProjectTask['status'], title: string) => {
    try {
      const columnTasks = tasks.filter((t) => t.status === status);
      const maxOrder = columnTasks.reduce((m, t) => Math.max(m, t.sort_order), 0);
      const newTask = await createProjectTask(projectId, {
        title,
        status,
      });
      // Ensure sort_order is set for local state even if backend returns 0
      newTask.sort_order = newTask.sort_order || maxOrder + 1;
      setTasks((prev) => [...prev, newTask]);
    } catch (err) {
      console.error('Failed to create task:', err);
    }
  };

  // Update task
  const handleUpdateTask = async (taskId: string, data: Partial<ProjectTask>) => {
    try {
      const updated = await updateProjectTask(projectId, taskId, data);
      setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)));
    } catch (err) {
      console.error('Failed to update task:', err);
    }
  };

  // Delete task
  const handleDeleteTask = async (taskId: string) => {
    try {
      await deleteProjectTask(projectId, taskId);
      setTasks((prev) => prev.filter((t) => t.id !== taskId));
    } catch (err) {
      console.error('Failed to delete task:', err);
    }
  };

  // Drag & Drop handlers
  const handleDragStart = (e: React.DragEvent, taskId: string) => {
    draggedTaskId.current = taskId;
    e.dataTransfer.effectAllowed = 'move';
    // Set minimal data to enable drag
    e.dataTransfer.setData('text/plain', taskId);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const handleDragEnter = (columnId: ColumnStatus) => {
    setDragOverColumn(columnId);
  };

  const handleDragLeave = () => {
    setDragOverColumn(null);
  };

  const handleDrop = async (e: React.DragEvent, columnId: ColumnStatus) => {
    e.preventDefault();
    setDragOverColumn(null);

    const taskId = draggedTaskId.current;
    if (!taskId) return;
    draggedTaskId.current = null;

    const task = tasks.find((t) => t.id === taskId);
    if (!task) return;

    const newStatus = STATUS_FOR_COLUMN[columnId];
    if (task.status === newStatus) return;

    // Optimistic update
    const updatedTasks = tasks.map((t) =>
      t.id === taskId ? { ...t, status: newStatus } : t
    );
    setTasks(updatedTasks);

    try {
      const columnTasks = updatedTasks.filter(
        (t) => t.id !== taskId && bucketForStatus(t.status) === columnId
      );
      const maxOrder = columnTasks.reduce((m, t) => Math.max(m, t.sort_order), 0);
      await updateProjectTask(projectId, taskId, {
        status: newStatus,
        sort_order: maxOrder + 1,
      });
    } catch (err) {
      console.error('Failed to move task:', err);
      // Revert on failure
      loadTasks();
    }
  };

  const bucketed = bucketTasks(tasks);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-8 h-8 text-indigo-400 animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-20">
        <p className="text-sm text-red-400">{error}</p>
      </div>
    );
  }

  return (
    <div className="flex gap-4 overflow-x-auto pb-4 min-h-[400px]">
      {COLUMNS.map((col) => (
        <div
          key={col.id}
          onDragEnter={() => handleDragEnter(col.id)}
          onDragLeave={handleDragLeave}
          className="flex-1 min-w-[280px] max-w-[360px]"
        >
          <KanbanColumn
            column={col}
            tasks={bucketed[col.id]}
            onCreateTask={handleCreateTask}
            onUpdateTask={handleUpdateTask}
            onDeleteTask={handleDeleteTask}
            onDragStart={handleDragStart}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            isDragOver={dragOverColumn === col.id}
            t={t}
          />
        </div>
      ))}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Utility: which column bucket does a status belong to?
// ---------------------------------------------------------------------------

function bucketForStatus(status: ProjectTask['status']): ColumnStatus {
  if (status === 'todo' || status === 'on_hold') return 'todo';
  if (status === 'in_progress') return 'in_progress';
  return 'done';
}

export default KanbanBoard;
