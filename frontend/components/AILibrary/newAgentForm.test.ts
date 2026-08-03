import { describe, it, expect } from 'vitest';
import {
  buildCreatePayload,
  validateForm,
  emptyForm,
  slugFromName,
  type NewAgentForm,
} from './newAgentForm';

const form = (over: Partial<NewAgentForm> = {}): NewAgentForm => ({
  ...emptyForm(),
  name: 'My Agent',
  slug: 'my-agent',
  ...over,
});

describe('buildCreatePayload', () => {
  it('carries the chosen template through as fork_from', () => {
    expect(buildCreatePayload(form({ template: 'script_ai' })).fork_from).toBe(
      'script_ai',
    );
  });

  it('sends no fork_from when starting from blank', () => {
    // '' would be a slug the backend then 404s on as a fork source.
    expect(buildCreatePayload(form({ template: null })).fork_from).toBeUndefined();
  });

  it('sends exactly one scope id', () => {
    const team = buildCreatePayload(form({ scope: 'team', teamId: '42' }));
    expect(team.team_id).toBe('42');
    expect(team.project_id).toBeUndefined();

    const project = buildCreatePayload(form({ scope: 'project', projectId: '7' }));
    expect(project.project_id).toBe('7');
    expect(project.team_id).toBeUndefined();

    const priv = buildCreatePayload(form({ scope: 'private' }));
    expect(priv.team_id).toBeUndefined();
    expect(priv.project_id).toBeUndefined();
  });

  it('keeps scope ids as strings', () => {
    // Snowflake BIGINT: Number() silently rounds past 2^53.
    const payload = buildCreatePayload(
      form({ scope: 'team', teamId: '9007199254740993' }),
    );
    expect(payload.team_id).toBe('9007199254740993');
  });

  it('trims name and drops an empty description', () => {
    const payload = buildCreatePayload(form({ name: '  Spaced  ', description: '   ' }));
    expect(payload.name).toBe('Spaced');
    expect(payload.description).toBeUndefined();
  });
});

describe('validateForm', () => {
  it('accepts a complete private form', () => {
    expect(validateForm(form())).toEqual([]);
  });

  it('requires a name', () => {
    expect(validateForm(form({ name: '   ' }))).toContain('name');
  });

  it('rejects a malformed slug', () => {
    expect(validateForm(form({ slug: 'Not A Slug' }))).toContain('slug');
    expect(validateForm(form({ slug: '' }))).toContain('slug');
  });

  it('rejects a slug already in use', () => {
    expect(validateForm(form({ slug: 'taken' }), ['taken'])).toContain('slugTaken');
  });

  it('requires the id that goes with the chosen scope', () => {
    // AgentCreate has no backend validator for this: submitting team scope
    // with no team lands as a private agent, silently ignoring the choice.
    expect(validateForm(form({ scope: 'team' }))).toContain('teamId');
    expect(validateForm(form({ scope: 'project' }))).toContain('projectId');
    expect(validateForm(form({ scope: 'team', teamId: '1' }))).toEqual([]);
  });
});

describe('slugFromName', () => {
  it('derives a valid slug so typing one is optional', () => {
    expect(slugFromName('My Custom Agent')).toBe('my-custom-agent');
    expect(slugFromName('Script AI 2.0')).toBe('script-ai-2-0');
  });

  it('never emits leading, trailing or doubled separators', () => {
    expect(slugFromName('  --Hello--  World!! ')).toBe('hello-world');
    expect(slugFromName('!!!')).toBe('');
  });
});
