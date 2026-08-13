// e2e-prod/helpers.ts — credential loading for the real-stack walkthrough.
//
// Credentials NEVER go in the repo. Two supported sources, in priority
// order:
//   1. DEBUG_TEST_EMAIL / DEBUG_TEST_PASSWORD environment variables.
//   2. A KEY=VALUE env file OUTSIDE the repo tree, path given by
//      CLAUDE_DEBUG_ENV_FILE (default: the path this repo's Claude debug
//      account convention already uses — see
//      memory/claude-debug-test-account.md — /media/heygo/program/datahub/
//      nous/secrets/claude-debug.env, mode 0600).
//
// See README.md for the full account/access story.

import { readFileSync } from 'node:fs';

export interface ProdCreds {
  email: string;
  password: string;
}

const DEFAULT_CREDS_FILE = '/media/heygo/program/datahub/nous/secrets/claude-debug.env';

function parseEnvFile(path: string): Record<string, string> {
  const text = readFileSync(path, 'utf8');
  const out: Record<string, string> = {};
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

export function loadProdCreds(): ProdCreds {
  if (process.env.DEBUG_TEST_EMAIL && process.env.DEBUG_TEST_PASSWORD) {
    return { email: process.env.DEBUG_TEST_EMAIL, password: process.env.DEBUG_TEST_PASSWORD };
  }

  const credsFile = process.env.CLAUDE_DEBUG_ENV_FILE || DEFAULT_CREDS_FILE;
  let parsed: Record<string, string>;
  try {
    parsed = parseEnvFile(credsFile);
  } catch (err) {
    throw new Error(
      `e2e-prod walkthrough needs credentials. Set DEBUG_TEST_EMAIL / DEBUG_TEST_PASSWORD env vars, ` +
        `or point CLAUDE_DEBUG_ENV_FILE at a KEY=VALUE file that has them ` +
        `(default: ${DEFAULT_CREDS_FILE}). Reading the default file failed: ${(err as Error).message}`,
    );
  }

  const email = parsed.DEBUG_TEST_EMAIL;
  const password = parsed.DEBUG_TEST_PASSWORD;
  if (!email || !password) {
    throw new Error(
      `Credentials file ${credsFile} is missing DEBUG_TEST_EMAIL and/or DEBUG_TEST_PASSWORD.`,
    );
  }
  return { email, password };
}

/** The real-stack target this walkthrough exercises.
 *
 * Default: the OWNER'S OWN production project (个人项目测试 1) — real
 * scripts, scenes, shots and a storyboard canvas the owner actually uses,
 * reached by the debug account through an explicit `project_members`
 * viewer row. That is deliberate and is the whole point of this suite:
 * a purpose-built QA fixture only proves the code works on data shaped
 * the way the test author imagined. Pointing the walkthrough at the real
 * project is what surfaced three defects a green fixture run never
 * would have (see README.md "Why the owner's real project"):
 *   - #1816 personal-project read gates ignored explicit member rows
 *   - #1817 a shared project's URL never mounted the workspace at all
 *   - #1820 a persisted viewport framing empty space rendered the canvas
 *     blank, and a viewer's autosave 403 looped on "Save failed"
 *
 * `teamId: 'personal'` is the personal-project URL convention (团队 id
 * literal, `projects.team_id IS NULL` — see ProjectsPage.tsx), not a
 * Snowflake id. Every field is env-overridable so the suite can be aimed
 * at the QA fixture team project (331438215859255 / 337650825568029 /
 * 337650825711390 / 337650952269612 / 337650953731886) or any other
 * target without touching the code. */
export const WALKTHROUGH_IDS = {
  teamId: process.env.PROD_TEST_TEAM_ID || 'personal',
  projectId: process.env.PROD_TEST_PROJECT_ID || '291022264100262',
  episodeId: process.env.PROD_TEST_EPISODE_ID || '324362669885098',
  sceneId: process.env.PROD_TEST_SCENE_ID || '324838427143194',
  shotId: process.env.PROD_TEST_SHOT_ID || '325601447269110',
};
