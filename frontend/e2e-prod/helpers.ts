// e2e-prod/helpers.ts — credential loading for the real-stack walkthrough.
//
// Credentials NEVER go in the repo. Sources, in priority order:
//   1. DEBUG_TEST_EMAIL / DEBUG_TEST_PASSWORD environment variables.
//   2. CLAUDE_DEBUG_ENV_FILE — an explicit path to a KEY=VALUE file.
//      If set but unreadable this THROWS; it does not fall through to the
//      defaults below. An explicit override that silently gets ignored is
//      worse than no override — you would be testing with the wrong account
//      and never know.
//   3. The default candidates below, first readable one wins.
//
// The default used to be a single gpupc-specific absolute path. That was
// correct while gpupc was also the dev machine; since 2026-09-07 it only
// runs production, and the dev machine is elsewhere — so the primary default
// is now home-relative and works on any machine. The gpupc path stays as a
// second candidate so nothing breaks if this suite is ever run there.
//
// See README.md for the full account/access story.

import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export interface ProdCreds {
  email: string;
  password: string;
}

/** Tried in order; first readable file wins. Both are OUTSIDE the repo. */
const DEFAULT_CREDS_FILES = [
  // Machine-agnostic — works on whichever box is the dev machine today.
  // Create with: mkdir -p ~/.nous && chmod 700 ~/.nous, file mode 0600.
  join(homedir(), '.nous', 'claude-debug.env'),
  // gpupc's historical convention (see memory/claude-debug-test-account.md).
  '/media/heygo/program/datahub/nous/secrets/claude-debug.env',
];

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

  // An explicitly-pointed file must exist. Falling back on failure would
  // silently run the suite against a different account than the operator
  // asked for.
  const explicit = process.env.CLAUDE_DEBUG_ENV_FILE;
  let credsFile: string;
  let parsed: Record<string, string>;

  if (explicit) {
    try {
      parsed = parseEnvFile(explicit);
      credsFile = explicit;
    } catch (err) {
      throw new Error(
        `CLAUDE_DEBUG_ENV_FILE points at ${explicit} but reading it failed: ` +
          `${(err as Error).message}. Not falling back to the defaults — an explicit ` +
          `override that gets silently ignored would run this suite against the wrong account.`,
      );
    }
  } else {
    const attempts: string[] = [];
    let found: { file: string; parsed: Record<string, string> } | null = null;
    for (const candidate of DEFAULT_CREDS_FILES) {
      try {
        found = { file: candidate, parsed: parseEnvFile(candidate) };
        break;
      } catch (err) {
        attempts.push(`  ${candidate} — ${(err as Error).message}`);
      }
    }
    if (!found) {
      throw new Error(
        `e2e-prod walkthrough needs credentials. Set DEBUG_TEST_EMAIL / DEBUG_TEST_PASSWORD ` +
          `env vars, or point CLAUDE_DEBUG_ENV_FILE at a KEY=VALUE file that has them, ` +
          `or create one of the default files (mode 0600):\n${attempts.join('\n')}`,
      );
    }
    credsFile = found.file;
    parsed = found.parsed;
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
