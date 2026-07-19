import { describe, it, expect } from 'vitest';
import {
  scopeQueryFilters,
  scopeCreateDefaults,
  scopeClientFilter,
  scopeCreatesInLabel,
  scopeIsReadOnly,
  type IssueScope,
} from './issueScope';
import type { UiIssue } from './types';
import type { Issue } from '../../services/issuesService';

// Snowflake-scale ids — 2^53 + 1 would round under Number(); we assert the
// strings survive untouched.
const TEAM = '9007199254740993';
const PROJECT = '9007199254740995';
const USER = '00000000-0000-4000-8000-000000000abc';
const AGENT = 'agent-77';

const SCOPES: Record<string, IssueScope> = {
  team: { type: 'team', teamId: TEAM },
  project: { type: 'project', teamId: TEAM, projectId: PROJECT },
  my: { type: 'my', teamId: TEAM, userId: USER },
  agent: { type: 'agent', teamId: TEAM, agentId: AGENT },
};

function mkIssue(over: Partial<Issue>): UiIssue {
  const raw = {
    id: 1,
    created_by_user_id: null,
    assignee_user_id: null,
    assignee_agent_id: null,
    ...over,
  } as unknown as Issue;
  return { id: 1, raw } as unknown as UiIssue;
}

describe('scopeQueryFilters', () => {
  it('team → team_id only, exact string', () => {
    expect(scopeQueryFilters(SCOPES.team)).toEqual({ team_id: TEAM });
  });
  it('project → team_id + project_id, both exact strings', () => {
    expect(scopeQueryFilters(SCOPES.project)).toEqual({ team_id: TEAM, project_id: PROJECT });
  });
  it('my → team_id only (narrowing is client-side)', () => {
    expect(scopeQueryFilters(SCOPES.my)).toEqual({ team_id: TEAM });
  });
  it('agent → team_id only (narrowing is client-side)', () => {
    expect(scopeQueryFilters(SCOPES.agent)).toEqual({ team_id: TEAM });
  });
  it('never coerces ids to number', () => {
    const f = scopeQueryFilters(SCOPES.project);
    expect(typeof f.team_id).toBe('string');
    expect(typeof f.project_id).toBe('string');
    expect(f.project_id).toBe(PROJECT); // not rounded to ...994
  });
});

describe('scopeCreateDefaults', () => {
  it('team → team_id only', () => {
    expect(scopeCreateDefaults(SCOPES.team)).toEqual({ team_id: TEAM });
  });
  it('project → team_id + project_id', () => {
    expect(scopeCreateDefaults(SCOPES.project)).toEqual({ team_id: TEAM, project_id: PROJECT });
  });
  it('my → team-level create (no project pin)', () => {
    expect(scopeCreateDefaults(SCOPES.my)).toEqual({ team_id: TEAM });
  });
  it('agent → team-level create shape (button is hidden, shape still valid)', () => {
    expect(scopeCreateDefaults(SCOPES.agent)).toEqual({ team_id: TEAM });
  });
  it('all values stay strings', () => {
    const d = scopeCreateDefaults(SCOPES.project);
    expect(typeof d.team_id).toBe('string');
    expect(typeof d.project_id).toBe('string');
  });
});

describe('scopeClientFilter', () => {
  const mine = mkIssue({ created_by_user_id: USER });
  const assignedToMe = mkIssue({ assignee_user_id: USER });
  const bothMine = mkIssue({ created_by_user_id: USER, assignee_user_id: USER });
  const someoneElses = mkIssue({ created_by_user_id: 'other', assignee_user_id: 'other' });
  const agentRow = mkIssue({ assignee_agent_id: AGENT });
  const otherAgentRow = mkIssue({ assignee_agent_id: 'agent-99' });

  it('team → passes everything', () => {
    const f = scopeClientFilter(SCOPES.team);
    expect([mine, someoneElses, agentRow].every(f)).toBe(true);
  });
  it('project → passes everything (backend already scoped)', () => {
    const f = scopeClientFilter(SCOPES.project);
    expect([mine, someoneElses, agentRow].every(f)).toBe(true);
  });
  it('my → created-by-me OR assigned-to-me, and rejects others', () => {
    const f = scopeClientFilter(SCOPES.my);
    expect(f(mine)).toBe(true);
    expect(f(assignedToMe)).toBe(true);
    expect(f(someoneElses)).toBe(false);
  });
  it('my → a both-mine row counts once (predicate evaluates each row once)', () => {
    const f = scopeClientFilter(SCOPES.my);
    const list = [bothMine, mine, assignedToMe, someoneElses];
    expect(list.filter(f)).toHaveLength(3); // no duplication of bothMine
  });
  it('agent → matches only this agent as assignee', () => {
    const f = scopeClientFilter(SCOPES.agent);
    expect(f(agentRow)).toBe(true);
    expect(f(otherAgentRow)).toBe(false);
    expect(f(mine)).toBe(false);
  });
});

describe('scopeCreatesInLabel', () => {
  it('team → "Creates in {teamName}"', () => {
    expect(scopeCreatesInLabel(SCOPES.team, { teamName: 'Team 8' })).toBe('Creates in Team 8');
  });
  it('team → falls back to "Team {id}" without a name', () => {
    expect(scopeCreatesInLabel({ type: 'team', teamId: '8' })).toBe('Creates in Team 8');
  });
  it('project → "Creates in {projectName} · {teamName}"', () => {
    expect(
      scopeCreatesInLabel(SCOPES.project, { teamName: 'Team 8', projectName: 'Neon Short' }),
    ).toBe('Creates in Neon Short · Team 8');
  });
  it('project → falls back to raw project id · team', () => {
    expect(
      scopeCreatesInLabel({ type: 'project', teamId: '8', projectId: '21321' }),
    ).toBe('Creates in 21321 · Team 8');
  });
  it('my → creates at team level', () => {
    expect(scopeCreatesInLabel(SCOPES.my, { teamName: 'Team 8' })).toBe('Creates in Team 8');
  });
  it('agent → read-only, creates nothing', () => {
    expect(scopeCreatesInLabel(SCOPES.agent, { teamName: 'Team 8' })).toBe('Read-only view');
  });
});

describe('scopeIsReadOnly', () => {
  it('only agent scope is read-only', () => {
    expect(scopeIsReadOnly(SCOPES.team)).toBe(false);
    expect(scopeIsReadOnly(SCOPES.project)).toBe(false);
    expect(scopeIsReadOnly(SCOPES.my)).toBe(false);
    expect(scopeIsReadOnly(SCOPES.agent)).toBe(true);
  });
});
