// frontend/components/AILibrary/newAgentForm.ts
// Form model for "New Agent" (B5, spec 2026-08-02 §B5).
//
// Pure, so the two rules that actually bite can be tested without a DOM:
//   - scope ids stay strings (Snowflake BIGINT loses precision through Number)
//   - the scope's id is required HERE, because AgentCreate has no backend
//     validator for it: a team-scoped submit with no team silently becomes a
//     private agent, and the user only finds out when nobody else can see it.

import type { CreateAgentPayload } from '../../types';

export type NewAgentScope = 'private' | 'team' | 'project';

export interface NewAgentForm {
  /** Slug of the template to fork; null = blank agent. */
  template: string | null;
  name: string;
  slug: string;
  description: string;
  scope: NewAgentScope;
  teamId: string;
  projectId: string;
}

export type NewAgentFormError =
  | 'name'
  | 'slug'
  | 'slugTaken'
  | 'teamId'
  | 'projectId';

const SLUG_PATTERN = /^[a-z0-9_-]+$/;

export function emptyForm(template: string | null = null): NewAgentForm {
  return {
    template,
    name: '',
    slug: '',
    description: '',
    scope: 'private',
    teamId: '',
    projectId: '',
  };
}

/** Derive a legal slug from a display name so typing one is optional. */
export function slugFromName(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function validateForm(
  form: NewAgentForm,
  takenSlugs: string[] = [],
): NewAgentFormError[] {
  const errors: NewAgentFormError[] = [];
  if (form.name.trim().length === 0) errors.push('name');
  if (!SLUG_PATTERN.test(form.slug)) errors.push('slug');
  else if (takenSlugs.includes(form.slug)) errors.push('slugTaken');
  if (form.scope === 'team' && form.teamId === '') errors.push('teamId');
  if (form.scope === 'project' && form.projectId === '') errors.push('projectId');
  return errors;
}

export function buildCreatePayload(form: NewAgentForm): CreateAgentPayload {
  return {
    slug: form.slug,
    name: form.name.trim(),
    description: form.description.trim() || undefined,
    // Empty string would be sent as a fork source the backend then 404s on.
    fork_from: form.template || undefined,
    team_id: form.scope === 'team' && form.teamId ? form.teamId : undefined,
    project_id:
      form.scope === 'project' && form.projectId ? form.projectId : undefined,
  };
}
