import { getAuthHeaders } from './parserService';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

// Project task interface (matches project_tasks DB table)
export interface ProjectTask {
  id: string;
  project_id: string;
  workflow_node_id: string | null;
  title: string;
  description: string | null;
  task_type: 'general' | 'storyboard' | 'script' | 'filming' | 'editing' | 'review';
  assignee_id: string | null;
  assignee_email?: string;
  due_date: string | null;
  sort_order: number;
  status: 'todo' | 'in_progress' | 'done' | 'cancelled' | 'on_hold';
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export type CreateProjectTaskData = {
  title: string;
  description?: string | null;
  task_type?: ProjectTask['task_type'];
  assignee_id?: string | null;
  due_date?: string | null;
  status?: ProjectTask['status'];
};

export type UpdateProjectTaskData = Partial<
  Pick<ProjectTask, 'title' | 'description' | 'task_type' | 'assignee_id' | 'due_date' | 'status' | 'sort_order'>
>;

// ============================================
// CRUD operations
// ============================================

export const fetchProjectTasks = async (projectId: string): Promise<ProjectTask[]> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/tasks`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch project tasks');
  const json = await response.json();
  return json.data || [];
};

export const createProjectTask = async (
  projectId: string,
  data: CreateProjectTaskData
): Promise<ProjectTask> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/tasks`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to create project task');
  }
  const json = await response.json();
  return json.data;
};

export const updateProjectTask = async (
  projectId: string,
  taskId: string,
  data: UpdateProjectTaskData
): Promise<ProjectTask> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/tasks/${taskId}`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to update project task');
  }
  const json = await response.json();
  return json.data;
};

export const deleteProjectTask = async (
  projectId: string,
  taskId: string
): Promise<void> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/tasks/${taskId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete project task');
};

// ============================================
// Reorder / move across columns
// ============================================

export const reorderTask = async (
  projectId: string,
  taskId: string,
  newStatus: ProjectTask['status'],
  newSortOrder: number
): Promise<ProjectTask> => {
  return updateProjectTask(projectId, taskId, {
    status: newStatus,
    sort_order: newSortOrder,
  });
};
