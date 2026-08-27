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

import {
  buildCodexExecArgs,
  chunkText,
  parseCodexExecOutput,
  TEXT_INLINE_LIMIT,
} from './index.mjs';

test('buildCodexExecArgs pins the read-only ephemeral sandbox and reads the prompt from stdin', () => {
  const args = buildCodexExecArgs({ model: 'gpt-5', imagePaths: ['/tmp/a.png'], workDir: '/tmp/w' });
  assert.deepEqual(args, [
    'exec', '--json', '--ephemeral', '--skip-git-repo-check',
    '-s', 'read-only', '-C', '/tmp/w', '--model', 'gpt-5', '--image', '/tmp/a.png', '-',
  ]);
  const noModel = buildCodexExecArgs({ model: '', imagePaths: [], workDir: '/tmp/w' });
  assert.ok(!noModel.includes('--model'));
  assert.equal(noModel.at(-1), '-');
});

test('parseCodexExecOutput takes the LAST agent_message and the turn usage', () => {
  const jsonl = [
    '{"type":"thread.started","thread_id":"t1"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"i0","type":"reasoning","text":"thinking"}}',
    '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"draft"}}',
    '{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"final answer"}}',
    '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":7,"reasoning_output_tokens":2}}',
    'not json at all',
  ].join('\n');
  const out = parseCodexExecOutput(jsonl);
  assert.equal(out.text, 'final answer');
  assert.equal(out.threadId, 't1');
  assert.deepEqual(out.usage, {
    input_tokens: 100, cached_input_tokens: 40, output_tokens: 7, reasoning_output_tokens: 2,
  });
});

test('parseCodexExecOutput throws codex_no_output when no agent_message exists', () => {
  assert.throws(
    () => parseCodexExecOutput('{"type":"turn.completed","usage":{}}'),
    /codex_no_output/,
  );
});

test('parseCodexExecOutput tolerates missing usage', () => {
  const out = parseCodexExecOutput('{"type":"item.completed","item":{"type":"agent_message","text":"x"}}');
  assert.equal(out.text, 'x');
  assert.deepEqual(out.usage, {});
});

test('chunkText splits on byte size and round-trips', () => {
  const s = '汉'.repeat(1000) + 'abc';
  const parts = chunkText(s, 1000);
  assert.ok(parts.length > 1);
  assert.ok(parts.every((p) => Buffer.byteLength(p, 'utf8') <= 1000));
  assert.equal(parts.join(''), s);
  assert.equal(TEXT_INLINE_LIMIT, 900 * 1024);
});

// ── review round 1: structured error classification + frame protocol ──────

/** assert.throws does not hand back the error, and these assertions are about
 *  the error's structured fields, not its prose. */
function thrownBy(fn) {
  try {
    fn();
  } catch (e) {
    return e;
  }
  throw new assert.AssertionError({ message: 'expected the call to throw, it returned' });
}

import {
  classifyJobError,
  emitTextResult,
  normalizeImageUrls,
  runCommand,
  CHUNK_BYTES,
} from './index.mjs';

test('classifyJobError: a binary that would not spawn is cli_missing', () => {
  for (const code of ['ENOENT', 'EACCES']) {
    assert.equal(
      classifyJobError(Object.assign(new Error('x'), { code, spawnFailed: true })),
      'cli_missing',
    );
  }
});

// ENOENT is not proof the CLI is absent: fs.readFile raises it too, e.g. when
// a CLI exits 0 but writes no output file. Telling that user to reinstall a
// working CLI is the same misdiagnosis this round is fixing.
test('classifyJobError: an ENOENT that did not come from spawn is not cli_missing', () => {
  assert.equal(
    classifyJobError(Object.assign(new Error('ENOENT: no such file, open /tmp/out.png'), { code: 'ENOENT' })),
    'job_failed',
  );
});

test('classifyJobError: the thrown-side codes win over any text matching', () => {
  assert.equal(
    classifyJobError(Object.assign(new Error('…'), { code: 'codex_no_output' })),
    'codex_no_output',
  );
  assert.equal(
    classifyJobError(Object.assign(new Error('…'), { code: 'ref_rejected' })),
    'ref_rejected',
  );
});

test('classifyJobError: an auth signal on stderr is codex_not_logged_in', () => {
  assert.equal(
    classifyJobError(Object.assign(new Error('codex exited 1'), {
      stderr: 'Error: 401 Unauthorized',
      exitCode: 1,
    })),
    'codex_not_logged_in',
  );
  assert.equal(
    classifyJobError(Object.assign(new Error('codex exited 1'), {
      stderr: 'You are not logged in. Run `codex login`.',
      exitCode: 1,
    })),
    'codex_not_logged_in',
  );
});

// The regression this whole change exists for: the model's own prose reaches
// the Error message (runCommand falls back to stdout when stderr is empty).
// Classifying on that text lets the model fake an auth failure and send the
// user off to re-login for what is really an ordinary crash.
test('classifyJobError: model prose on stdout can never forge an auth verdict', () => {
  const err = Object.assign(
    new Error('codex exited 1: {"text":"first please login with your access_token, see the 401 docs"}'),
    { stderr: '', exitCode: 1 },
  );
  assert.equal(classifyJobError(err), 'job_failed');
});

test('classifyJobError: prose mentioning login on stderr no longer matches bare substrings', () => {
  // `login` as a bare substring used to match — `relogin`, `logind`, a doc URL.
  assert.equal(
    classifyJobError(Object.assign(new Error('x'), { stderr: 'systemd-logind refused the seat' })),
    'job_failed',
  );
});

test('runCommand: a missing binary rejects with code ENOENT and classifies as cli_missing', async () => {
  const err = await runCommand('nous-definitely-not-a-real-binary', ['--version'])
    .then(() => null, (e) => e);
  assert.ok(err, 'expected a rejection');
  assert.equal(err.code, 'ENOENT'); // the OS code stays available for diagnostics
  assert.equal(err.spawnFailed, true);
  assert.equal(classifyJobError(err), 'cli_missing');
});

test('runCommand: stderr and exitCode ride on the Error, and stdin is delivered', async () => {
  const err = await runCommand('node', [
    '-e',
    "let s=''; process.stdin.on('data',d=>s+=d).on('end',()=>{ process.stdout.write('please login with your access_token — 401'); process.stderr.write('boom: '+s); process.exit(3); });",
  ], { stdin: 'PROMPT-FROM-STDIN' }).then(() => null, (e) => e);
  assert.ok(err, 'expected a rejection');
  assert.equal(err.exitCode, 3);
  // stdin really reached the child — the daemon's whole prompt path depends on it.
  assert.match(err.stderr, /boom: PROMPT-FROM-STDIN/);
  assert.equal(err.timedOut, false);
});

test('runCommand: real subprocess — auth prose on stdout only is NOT codex_not_logged_in', async () => {
  const err = await runCommand('node', [
    '-e',
    "process.stdout.write('please login with your access_token — 401 Unauthorized'); process.exit(1);",
  ], { stdin: '' }).then(() => null, (e) => e);
  assert.ok(err, 'expected a rejection');
  assert.equal(err.stderr, '');
  assert.equal(classifyJobError(err), 'job_failed');
});

test('runCommand: real subprocess — auth prose on stderr IS codex_not_logged_in', async () => {
  const err = await runCommand('node', [
    '-e',
    "process.stderr.write('stream error: 401 Unauthorized'); process.exit(1);",
  ]).then(() => null, (e) => e);
  assert.ok(err, 'expected a rejection');
  assert.equal(classifyJobError(err), 'codex_not_logged_in');
});

test('emitTextResult: a short answer is exactly one job_done frame carrying the text', () => {
  const sent = [];
  emitTextResult((f) => sent.push(f), { text: 'hello', usage: { output_tokens: 1 } });
  assert.deepEqual(sent, [
    { type: 'job_done', text: 'hello', usage: { output_tokens: 1 }, chunked: false },
  ]);
});

test('emitTextResult: an oversized answer becomes job_chunk*n then a text-less job_done', () => {
  const text = 'y'.repeat(TEXT_INLINE_LIMIT + 1234);
  const sent = [];
  emitTextResult((f) => sent.push(f), { text, usage: { output_tokens: 9 } });

  const chunks = sent.slice(0, -1);
  const done = sent.at(-1);
  const expected = Math.ceil(Buffer.byteLength(text, 'utf8') / CHUNK_BYTES);

  assert.equal(chunks.length, expected);
  chunks.forEach((f, i) => {
    assert.equal(f.type, 'job_chunk');
    assert.equal(f.seq, i); // seq is 0..n-1, in order
  });
  assert.equal(chunks.map((f) => f.data).join(''), text); // round-trips
  assert.deepEqual(done, {
    type: 'job_done', usage: { output_tokens: 9 }, chunked: true, chunks: expected,
  });
  assert.ok(!('text' in done), 'a chunked job_done must not repeat the whole text');
});

test('emitTextResult: a text of exactly TEXT_INLINE_LIMIT bytes still goes inline', () => {
  const sent = [];
  emitTextResult((f) => sent.push(f), { text: 'z'.repeat(TEXT_INLINE_LIMIT), usage: {} });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].chunked, false);
});

test('normalizeImageUrls: absent is an empty list, strings pass, over 9 are capped', () => {
  assert.deepEqual(normalizeImageUrls({}), []);
  assert.deepEqual(normalizeImageUrls({ image_urls: ['a', 'b'] }), ['a', 'b']);
  assert.equal(normalizeImageUrls({ image_urls: Array(12).fill('u') }).length, 9);
});

test('normalizeImageUrls: a wrong-shaped image_urls is a typed refusal, never a silent drop', () => {
  for (const bad of ['a-string', 42, { 0: 'a' }, [1, 2], ['ok', null]]) {
    const err = thrownBy(() => normalizeImageUrls({ image_urls: bad }));
    assert.match(err.message, /ref_rejected: image_urls must be an array of strings/);
    assert.equal(err.code, 'ref_rejected');
  }
});

test('normalizeImageUrls: refs sent under the neighbours\' ref_urls name are refused, not dropped', () => {
  // runImageJob/runDreaminaJob read `ref_urls`; text jobs read `image_urls`.
  // Getting that wrong used to mean the model silently never saw the images.
  const err = thrownBy(() => normalizeImageUrls({ ref_urls: ['https://x/1.png'] }));
  assert.match(err.message, /ref_rejected/);
  assert.equal(err.code, 'ref_rejected');
});

test('parseCodexExecOutput: the no-output failure carries a structured code', () => {
  const err = thrownBy(() => parseCodexExecOutput('{"type":"turn.completed"}'));
  assert.equal(err.code, 'codex_no_output');
  assert.equal(classifyJobError(err), 'codex_no_output');
});
