import { apiClient } from './apiClient';

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

interface Envelope<T> {
  data?: T;
}

// ============================================
// CRUD operations
// ============================================

export const fetchProjectTasks = async (
  projectId: string,
): Promise<ProjectTask[]> => {
  const response = await apiClient.get<Envelope<ProjectTask[]>>(
    `/api/v1/projects/${projectId}/tasks`,
  );
  return response.data || [];
};

export const createProjectTask = async (
  projectId: string,
  data: CreateProjectTaskData,
): Promise<ProjectTask> => {
  const response = await apiClient.post<Envelope<ProjectTask>>(
    `/api/v1/projects/${projectId}/tasks`,
    data,
  );
  if (!response.data) throw new Error('Empty response from createProjectTask');
  return response.data;
};

export const updateProjectTask = async (
  projectId: string,
  taskId: string,
  data: UpdateProjectTaskData,
): Promise<ProjectTask> => {
  const response = await apiClient.put<Envelope<ProjectTask>>(
    `/api/v1/projects/${projectId}/tasks/${taskId}`,
    data,
  );
  if (!response.data) throw new Error('Empty response from updateProjectTask');
  return response.data;
};

export const deleteProjectTask = async (
  projectId: string,
  taskId: string,
): Promise<void> => {
  await apiClient.delete(`/api/v1/projects/${projectId}/tasks/${taskId}`);
};

// ============================================
// Reorder / move across columns
// ============================================

export const reorderTask = async (
  projectId: string,
  taskId: string,
  newStatus: ProjectTask['status'],
  newSortOrder: number,
): Promise<ProjectTask> =>
  updateProjectTask(projectId, taskId, {
    status: newStatus,
    sort_order: newSortOrder,
  });
