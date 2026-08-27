// tools/codex-daemon/index.test.mjs — `node --test`
//
// Only the pure helpers are exercised here: the daemon's real work is a
// WebSocket loop and three whitelisted subprocesses, which the real-machine
// checklist in the PR covers. What is tested here is exactly what used to be
// wrong by inspection — close-code triage, version parsing, unit/plist
// content, and --name handling.

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import {
  clearLastStop,
  describeLastStop,
  isTerminalClose,
  parsePairArgs,
  parseVersion,
  readLastStop,
  escapeXml,
  renderLaunchdPlist,
  renderSystemdUnit,
  stopReasonForCloseCode,
  systemdQuote,
  writeLastStop,
} from './index.mjs';

const tmpdir = () => fs.mkdtemp(path.join(os.tmpdir(), 'nous-codex-test-'));

test('isTerminalClose: revocation and auth failure are final', () => {
  // codex_daemon_ws_router.py closes 4001; daemon_registry.py closes 4003.
  assert.equal(isTerminalClose(4001), true);
  assert.equal(isTerminalClose(4003), true);
});

test('isTerminalClose: transport-level closes stay retryable', () => {
  for (const code of [1000, 1001, 1006, 1011, 1012, 4000, 4002, 4004, null, undefined]) {
    assert.equal(isTerminalClose(code), false, `code ${code} must be retryable`);
  }
});

test('parseVersion: dreamina JSON output yields a bare version', () => {
  // Real shape from `dreamina --version`: a JSON object, not a version line.
  const out = '{"name": "dreamina-cli", "version": "1.4.2", "build": 20260731}';
  assert.equal(parseVersion(out), '1.4.2');
});

test('parseVersion: the quoted version key wins over an earlier bare number', () => {
  const out = '{"schema": 2.0, "version": "0.9.11"}';
  assert.equal(parseVersion(out), '0.9.11');
});

test('parseVersion: plain-text CLIs still work', () => {
  assert.equal(parseVersion('codex-cli 0.52.0\n'), '0.52.0');
  assert.equal(parseVersion('gpt-image-2-skill v0.7.3'), '0.7.3');
});

test('parseVersion: unparseable but present output is not mistaken for absent', () => {
  assert.equal(parseVersion('unknown'), 'unknown');
  assert.equal(parseVersion(''), 'installed');
  assert.equal(parseVersion(null), 'installed');
});

test('stopReasonForCloseCode: the two terminal closes are told apart', () => {
  assert.equal(stopReasonForCloseCode(4003), 'revoked');
  assert.equal(stopReasonForCloseCode(4001), 'auth_failed');
});

test('stopReasonForCloseCode: retryable closes record nothing', () => {
  for (const code of [1000, 1001, 1006, 1011, 4000, 4002, 4004, null, undefined]) {
    assert.equal(stopReasonForCloseCode(code), null, `code ${code} must not be terminal`);
  }
});

test('describeLastStop: a recorded revocation reads as an instruction', () => {
  const line = describeLastStop({ reason: 'revoked', at: '2026-08-26T12:28:17.101Z' });
  assert.match(line, /revoked/);
  assert.match(line, /re-pair/);
  assert.match(line, /2026-08-26T12:28:17\.101Z/);
  assert.match(describeLastStop({ reason: 'auth_failed', at: 'x' }), /token is no longer valid/);
  // Both sentences must carry the fix, not just name the problem.
  assert.match(describeLastStop({ reason: 'auth_failed', at: 'x' }), /re-pair/);
});

test('describeLastStop: nothing, or an unrecognised reason, prints nothing', () => {
  // An unknown reason must not be rendered as if it were understood — that is
  // how a stale/foreign file would turn into a confident wrong explanation.
  assert.equal(describeLastStop(null), null);
  assert.equal(describeLastStop(undefined), null);
  assert.equal(describeLastStop('revoked'), null);
  assert.equal(describeLastStop({ reason: 'something_else', at: 'x' }), null);
  assert.equal(describeLastStop({}), null);
});

test('last_stop.json: write → read round-trips, and clear removes it', async () => {
  const dir = await tmpdir();
  try {
    assert.equal(await readLastStop(dir), null, 'absent file reads as null');

    await writeLastStop('revoked', dir);
    const entry = await readLastStop(dir);
    assert.equal(entry.reason, 'revoked');
    assert.ok(!Number.isNaN(Date.parse(entry.at)), 'at is an ISO timestamp');
    assert.match(describeLastStop(entry), /re-pair/);

    // pair() calls this: a fresh pairing must not leave `status` reporting a
    // revocation that no longer applies.
    await clearLastStop(dir);
    assert.equal(await readLastStop(dir), null);
    // Clearing an already-absent file is not an error (pair on a fresh box).
    await clearLastStop(dir);
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});

test('last_stop.json: the file is private to the user', async () => {
  const dir = await tmpdir();
  try {
    await writeLastStop('auth_failed', dir);
    const st = await fs.stat(path.join(dir, 'last_stop.json'));
    assert.equal(st.mode & 0o077, 0, 'no group/other bits');
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});

test('renderSystemdUnit: restarts on crash but never after a revocation', () => {
  const unit = renderSystemdUnit({
    nodePath: '/usr/bin/node',
    scriptPath: '/home/u/.local/share/nous-codex/nous-codex.mjs',
  });
  assert.match(unit, /^ExecStart="\/usr\/bin\/node" "\/home\/u\/\.local\/share\/nous-codex\/nous-codex\.mjs" run$/m);
  assert.match(unit, /^Restart=on-failure$/m);
  assert.match(unit, /^RestartSec=5$/m);
  assert.match(unit, /^WantedBy=default\.target$/m);
  // The revocation guard is Restart=on-failure + a clean exit 0, so there is
  // no exit-status exception to declare — and Restart=always would defeat it.
  assert.doesNotMatch(unit, /RestartPreventExitStatus/);
  assert.doesNotMatch(unit, /^Restart=always$/m);
  // No API base set → no Environment line at all (not an empty one).
  assert.doesNotMatch(unit, /^Environment=/m);
});

test('renderSystemdUnit: NOUS_API_BASE is carried into the unit when set', () => {
  const unit = renderSystemdUnit({
    nodePath: '/usr/bin/node',
    scriptPath: '/x/nous-codex.mjs',
    apiBase: 'http://10.0.0.10:8080',
  });
  assert.match(unit, /^Environment=NOUS_API_BASE="http:\/\/10\.0\.0\.10:8080"$/m);
});

test('renderSystemdUnit: the installing shell PATH is baked in', () => {
  // systemd --user gives services a minimal PATH: without this the daemon
  // starts fine and then reports codex / gpt-image-2-skill / dreamina all
  // missing, bouncing every job with cli_missing. Verified on a real box.
  const unit = renderSystemdUnit({
    nodePath: '/usr/bin/node',
    scriptPath: '/x/nous-codex.mjs',
    pathEnv: '/home/u/.local/bin:/usr/bin:/bin',
  });
  assert.match(unit, /^Environment=PATH="\/home\/u\/\.local\/bin:\/usr\/bin:\/bin"$/m);
});

test('renderLaunchdPlist: PATH and API base share one EnvironmentVariables dict', () => {
  const plist = renderLaunchdPlist({
    nodePath: '/usr/local/bin/node',
    scriptPath: '/x/nous-codex.mjs',
    logPath: '/x/nous-codex.log',
    apiBase: 'https://api.example.test',
    pathEnv: '/opt/homebrew/bin:/usr/bin',
  });
  const dicts = plist.match(/<key>EnvironmentVariables<\/key>/g) ?? [];
  assert.equal(dicts.length, 1, 'exactly one EnvironmentVariables dict');
  assert.match(plist, /<key>PATH<\/key>\s*<string>\/opt\/homebrew\/bin:\/usr\/bin<\/string>/);
  assert.match(plist, /<key>NOUS_API_BASE<\/key>\s*<string>https:\/\/api\.example\.test<\/string>/);
});

test('renderLaunchdPlist: label, argv, RunAtLoad and log paths', () => {
  const plist = renderLaunchdPlist({
    nodePath: '/opt/homebrew/bin/node',
    scriptPath: '/Users/u/.local/share/nous-codex/nous-codex.mjs',
    logPath: '/Users/u/Library/Logs/nous-codex.log',
  });
  assert.match(plist, /<string>ink\.nous\.codex<\/string>/);
  assert.match(plist, /<string>\/opt\/homebrew\/bin\/node<\/string>/);
  assert.match(plist, /<string>\/Users\/u\/\.local\/share\/nous-codex\/nous-codex\.mjs<\/string>/);
  assert.match(plist, /<key>RunAtLoad<\/key>\s*<true\/>/);
  // KeepAlive must be the dict form, not a bare <true/> that respawns forever.
  assert.match(plist, /<key>KeepAlive<\/key>\s*<dict>\s*<key>SuccessfulExit<\/key>\s*<false\/>\s*<\/dict>/);
  assert.match(plist, /<key>StandardOutPath<\/key>\s*<string>\/Users\/u\/Library\/Logs\/nous-codex\.log<\/string>/);
  assert.match(plist, /<key>StandardErrorPath<\/key>\s*<string>\/Users\/u\/Library\/Logs\/nous-codex\.log<\/string>/);
  assert.doesNotMatch(plist, /EnvironmentVariables/);
});

test('renderLaunchdPlist: NOUS_API_BASE becomes an EnvironmentVariables dict', () => {
  const plist = renderLaunchdPlist({
    nodePath: '/usr/local/bin/node',
    scriptPath: '/x/nous-codex.mjs',
    logPath: '/x/nous-codex.log',
    apiBase: 'https://api.example.test',
  });
  assert.match(
    plist,
    /<key>EnvironmentVariables<\/key>\s*<dict>\s*<key>NOUS_API_BASE<\/key>\s*<string>https:\/\/api\.example\.test<\/string>\s*<\/dict>/,
  );
});

test('parsePairArgs: bare code, no name', () => {
  assert.deepEqual(parsePairArgs(['ABCD2345']), { code: 'ABCD2345', name: null });
});

test('parsePairArgs: --name in both spellings', () => {
  assert.deepEqual(parsePairArgs(['ABCD2345', '--name', 'studio-mac']), {
    code: 'ABCD2345',
    name: 'studio-mac',
  });
  assert.deepEqual(parsePairArgs(['--name=studio-mac', 'ABCD2345']), {
    code: 'ABCD2345',
    name: 'studio-mac',
  });
});

test('parsePairArgs: the name value is never mistaken for the code', () => {
  assert.deepEqual(parsePairArgs(['--name', 'gpupc', 'ABCD2345']), {
    code: 'ABCD2345',
    name: 'gpupc',
  });
});

test('parsePairArgs: blank or missing name falls back to null (caller uses hostname)', () => {
  assert.equal(parsePairArgs(['ABCD2345', '--name', '   ']).name, null);
  assert.equal(parsePairArgs(['ABCD2345', '--name']).name, null);
  assert.equal(parsePairArgs([]).code, null);
});


test('systemdQuote: a PATH with spaces survives as ONE value', () => {
  // Unquoted, systemd splits on whitespace and ExecStart/PATH is silently
  // truncated at the first space — the symptom is every CLI "missing".
  assert.equal(systemdQuote('/home/my tools/bin:/usr/bin'), '"/home/my tools/bin:/usr/bin"');
});

test('systemdQuote: % is doubled and backslash/quote are escaped', () => {
  // systemd expands %x specifiers even inside quotes, so a literal % must be
  // written %%; a bare " or \ would otherwise end (or corrupt) the value.
  assert.equal(systemdQuote('a%b'), '"a%%b"');
  assert.equal(systemdQuote('a"b'), '"a\\"b"');
  assert.equal(systemdQuote('a\\b'), '"a\\\\b"');
});

test('renderSystemdUnit: paths with spaces and % are quoted in place', () => {
  const unit = renderSystemdUnit({
    nodePath: '/opt/my node/bin/node',
    scriptPath: '/home/a b/nous-codex.mjs',
    pathEnv: '/home/a b/.local/bin:/usr/bin',
    apiBase: 'http://h/%2Fx',
  });
  assert.match(unit, /^ExecStart="\/opt\/my node\/bin\/node" "\/home\/a b\/nous-codex\.mjs" run$/m);
  assert.match(unit, /^Environment=PATH="\/home\/a b\/\.local\/bin:\/usr\/bin"$/m);
  assert.match(unit, /^Environment=NOUS_API_BASE="http:\/\/h\/%%2Fx"$/m);
});

test('escapeXml: plist-breaking characters are neutralised', () => {
  assert.equal(escapeXml('a&b<c>"d\'e'), 'a&amp;b&lt;c&gt;&quot;d&apos;e');
});

test('renderLaunchdPlist: an & in a path does not produce invalid XML', () => {
  const plist = renderLaunchdPlist({
    nodePath: '/usr/bin/node',
    scriptPath: '/Users/a&b/nous-codex.mjs',
    logPath: '/Users/a&b/nous-codex.log',
    apiBase: 'https://h/?x=1&y=2',
    pathEnv: '/Users/a&b/bin',
  });
  assert.match(plist, /<string>\/Users\/a&amp;b\/nous-codex\.mjs<\/string>/);
  assert.match(plist, /<string>https:\/\/h\/\?x=1&amp;y=2<\/string>/);
  // A bare & anywhere would make launchd reject the plist.
  assert.doesNotMatch(plist, /&(?!amp;|lt;|gt;|quot;|apos;)/);
});
