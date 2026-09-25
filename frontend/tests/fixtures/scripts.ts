/**
 * Wire-shape fixtures for the /api/v1/scripts/projects domain.
 *
 * Every field the generated schema marks required is present, and Snowflake
 * ids are JSON numbers exactly as the backend sends them (CLAUDE.md,
 * "边界 mock 必须用真实 JSON 形状"). Tests override only what they assert on.
 */
import type { ScriptChapter, ScriptProject } from '../../types/api';

/** One `script_chapters` row. */
export function makeScriptChapter(overrides: Partial<ScriptChapter> = {}): ScriptChapter {
  return {
    id: 100,
    script_id: 1,
    parent_chapter_id: null,
    chapter_number: 1,
    title: 'Chapter',
    summary: null,
    content: null,
    branch_label: null,
    branch_type: null,
    position_x: 0,
    position_y: 0,
    width: null,
    height: null,
    data_json: {},
    sort_order: 0,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    content_json: null,
    ...overrides,
  };
}

/** One `script_projects` row. */
export function makeScriptProject(overrides: Partial<ScriptProject> = {}): ScriptProject {
  return {
    id: 1,
    project_id: 10,
    team_id: 20,
    name: 'Script',
    created_by: 'u1',
    display_code: null,
    description: null,
    settings_json: {},
    viewport_json: null,
    status: 'active',
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    genre: null,
    episode_id: null,
    target_duration_sec: null,
    numbering_locked_at: null,
    ...overrides,
  };
}
