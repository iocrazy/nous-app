// API layer for /api/v1/ideation/topics/* (Project Workflow M1.5).
//
// The ideation topic pool is the global "before you make a project" surface.
// Endpoints live under /ideation/topics (NOT /topics — that prefix belongs to
// the hotspots/signal-feed router). Named ideationService (not topicsService)
// to avoid confusion with the singular topicService.ts, which is hotspots.

import { Topic, TopicStatus } from '../types';
import { apiClient } from './apiClient';

interface Envelope<T> {
  data?: T;
}

/** At most one source reference — everything else is a from-scratch topic. */
export interface TopicCreatePayload {
  title: string;
  cover_url?: string | null;
  excerpt?: string | null;
  note_id?: string;
  resource_id?: string;
  media_id?: string;
  inspiration_topic_id?: string;
}

export interface TopicUpdatePayload {
  title?: string;
  cover_url?: string | null;
  excerpt?: string | null;
  status?: TopicStatus;
}

/** List a team's topics, optionally filtered by status. */
export const fetchTopics = async (
  teamId: string,
  status?: TopicStatus,
): Promise<Topic[]> => {
  const response = await apiClient.get<Envelope<Topic[]>>(
    '/api/v1/ideation/topics',
    { query: { team_id: teamId, status } },
  );
  return response.data || [];
};

export const createTopic = async (
  teamId: string,
  payload: TopicCreatePayload,
): Promise<Topic> => {
  const response = await apiClient.post<Envelope<Topic>>(
    '/api/v1/ideation/topics',
    payload,
    { query: { team_id: teamId } },
  );
  if (!response.data) throw new Error('Empty response from createTopic');
  return response.data;
};

export const updateTopic = async (
  topicId: string,
  patch: TopicUpdatePayload,
): Promise<Topic> => {
  const response = await apiClient.patch<Envelope<Topic>>(
    `/api/v1/ideation/topics/${topicId}`,
    patch,
  );
  if (!response.data) throw new Error('Empty response from updateTopic');
  return response.data;
};

export const deleteTopic = async (topicId: string): Promise<void> => {
  await apiClient.delete(`/api/v1/ideation/topics/${topicId}`);
};
