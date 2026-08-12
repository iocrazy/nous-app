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

/** The real-stack QA fixture this walkthrough exercises — see README.md
 * "Fixture data" for how it was created and why it isn't the project id
 * an older memory note mentions (that one 403s for the debug account). */
export const WALKTHROUGH_IDS = {
  teamId: process.env.PROD_TEST_TEAM_ID || '331438215859255',
  projectId: process.env.PROD_TEST_PROJECT_ID || '337650825568029',
  episodeId: process.env.PROD_TEST_EPISODE_ID || '337650825711390',
  sceneId: process.env.PROD_TEST_SCENE_ID || '337650952269612',
  shotId: process.env.PROD_TEST_SHOT_ID || '337650953731886',
};
