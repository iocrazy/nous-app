// tools/codex-daemon/index.test.mjs — `node --test`
//
// Only the pure helpers are exercised here: the daemon's real work is a
// WebSocket loop and three whitelisted subprocesses, which the real-machine
// checklist in the PR covers. What is tested here is exactly what used to be
// wrong by inspection — close-code triage, version parsing, unit/plist
// content, and --name handling.

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  isTerminalClose,
  parsePairArgs,
  parseVersion,
  renderLaunchdPlist,
  renderSystemdUnit,
} from './index.mjs';

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

test('renderSystemdUnit: restarts on crash but never after a revocation', () => {
  const unit = renderSystemdUnit({
    nodePath: '/usr/bin/node',
    scriptPath: '/home/u/.local/share/nous-codex/nous-codex.mjs',
  });
  assert.match(unit, /^ExecStart=\/usr\/bin\/node \/home\/u\/\.local\/share\/nous-codex\/nous-codex\.mjs run$/m);
  assert.match(unit, /^Restart=on-failure$/m);
  assert.match(unit, /^RestartSec=5$/m);
  assert.match(unit, /^RestartPreventExitStatus=2$/m);
  assert.match(unit, /^WantedBy=default\.target$/m);
  // No API base set → no Environment line at all (not an empty one).
  assert.doesNotMatch(unit, /^Environment=/m);
});

test('renderSystemdUnit: NOUS_API_BASE is carried into the unit when set', () => {
  const unit = renderSystemdUnit({
    nodePath: '/usr/bin/node',
    scriptPath: '/x/nous-codex.mjs',
    apiBase: 'http://10.0.0.10:8080',
  });
  assert.match(unit, /^Environment=NOUS_API_BASE=http:\/\/10\.0\.0\.10:8080$/m);
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
