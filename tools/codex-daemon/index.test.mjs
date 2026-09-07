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
  HeartbeatLiveness,
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

// The backend's _STATUS_BY_CODE has a real `timeout` code (424) but no
// `job_failed` key, so a timed-out run used to be coerced to `codex_failed` —
// its timeout branch never fired in the one case that reaches it most.
test('classifyJobError: a timed-out run is timeout, not a generic failure', () => {
  assert.equal(
    classifyJobError(Object.assign(new Error('codex timed out after 180s'), {
      timedOut: true, exitCode: null, stderr: '',
    })),
    'timeout',
  );
});

// SIGKILL races the child's own exit, so a timed-out run can still arrive
// with an exit code and late stderr. The timeout is the true story.
test('classifyJobError: timeout outranks a late exit code and auth-looking stderr', () => {
  assert.equal(
    classifyJobError(Object.assign(new Error('codex timed out after 180s'), {
      timedOut: true, exitCode: 1, stderr: 'stream error: 401 Unauthorized',
    })),
    'timeout',
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

// ── inline data:image refs + typed ref refusals ───────────────────────────

import { downloadRef, parseDataImageUrl, REF_MAX_BYTES } from './index.mjs';

const PNG_B64 = Buffer.from('\x89PNG\r\n\x1a\n-pretend-pixels-', 'binary').toString('base64');

test('parseDataImageUrl: a base64 png yields its mime and exact bytes', () => {
  const { mime, bytes } = parseDataImageUrl(`data:image/png;base64,${PNG_B64}`);
  assert.equal(mime, 'image/png');
  assert.equal(bytes.toString('base64'), PNG_B64);
});

test('parseDataImageUrl: a non-image mime is refused', () => {
  for (const mime of ['text/plain', 'application/pdf', 'text/html']) {
    const err = thrownBy(() => parseDataImageUrl(`data:${mime};base64,${PNG_B64}`));
    assert.equal(err.code, 'ref_rejected');
  }
});

// SVG is image/* but carries script; codex would be reading an attacker-
// supplied document, so it stays off the allowlist with the rest.
test('parseDataImageUrl: image/svg+xml is refused despite being image/*', () => {
  const err = thrownBy(() => parseDataImageUrl(`data:image/svg+xml;base64,${PNG_B64}`));
  assert.equal(err.code, 'ref_rejected');
});

test('parseDataImageUrl: malformed base64 is refused, not silently half-decoded', () => {
  // Buffer.from(..., 'base64') is lenient: it drops junk and returns bytes.
  // Without an explicit check these would sail through as a corrupt image.
  for (const bad of ['not*base64!!', 'AAAA===', 'AAA', '', '@@@@']) {
    const err = thrownBy(() => parseDataImageUrl(`data:image/png;base64,${bad}`));
    assert.equal(err.code, 'ref_rejected');
  }
});

test('parseDataImageUrl: a payload over the size cap is refused', () => {
  const big = Buffer.alloc(REF_MAX_BYTES + 1, 0x41).toString('base64');
  const err = thrownBy(() => parseDataImageUrl(`data:image/png;base64,${big}`));
  assert.equal(err.code, 'ref_rejected');
  // and exactly at the cap is still fine
  const atCap = Buffer.alloc(REF_MAX_BYTES, 0x41).toString('base64');
  assert.equal(parseDataImageUrl(`data:image/png;base64,${atCap}`).bytes.length, REF_MAX_BYTES);
});

test('parseDataImageUrl: a non-base64 data URL is refused', () => {
  assert.equal(thrownBy(() => parseDataImageUrl('data:image/png,rawbytes')).code, 'ref_rejected');
});

test('downloadRef: a data:image ref is written locally with the mime-derived extension', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'refs-'));
  const file = await downloadRef(`data:image/webp;base64,${PNG_B64}`, dir, 3);
  assert.equal(path.basename(file), 'ref-3.webp');
  assert.equal((await fs.readFile(file)).toString('base64'), PNG_B64);
  await fs.rm(dir, { recursive: true, force: true });
});

// "Local Codex failed to produce a reply" is a lie when the daemon simply
// refused an off-host image — nothing about codex or the user's machine
// is broken, the request just has to carry a different attachment.
test('downloadRef: an off-host http url is a typed ref_rejected, not a generic failure', async () => {
  const err = await downloadRef('https://evil.example/pic.png', '/tmp', 0).then(() => null, (e) => e);
  assert.ok(err, 'expected a rejection');
  assert.equal(err.code, 'ref_rejected');
  assert.equal(classifyJobError(err), 'ref_rejected');
});

test('normalizeImageUrls: inline data:image refs pass the payload check', () => {
  const urls = [`data:image/png;base64,${PNG_B64}`, 'https://api.nous.ink/x.png'];
  assert.deepEqual(normalizeImageUrls({ image_urls: urls }), urls);
});

// ── SSRF guard applies to redirects, not just the URL we were handed ──────
//
// `fetch` follows redirects by default, so checking only the initial string
// left the allowlist governing the first request alone — a nous endpoint
// that 302s (generated-media presigns that way) was the way out. These use a
// stub fetch, which cannot itself follow anything; that is exactly why the
// first test below asserts the `redirect: 'manual'` option is passed. Without
// that assertion every test here stays green on code that follows redirects
// blind, because the stub hands back the 3xx either way.

import { assertNousUrl, REF_MAX_REDIRECTS } from './index.mjs';

const NOUS = 'https://api.nous.ink';

function redirectRes(location, status = 302) {
  return {
    status,
    ok: false,
    headers: { get: (k) => (String(k).toLowerCase() === 'location' ? location : null) },
  };
}

function okRes(buf) {
  return {
    status: 200,
    ok: true,
    headers: { get: () => null },
    arrayBuffer: async () => buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength),
  };
}

/** Installs a scripted fetch; returns the call log and a restore fn. */
function stubFetch(responses) {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url: String(url), opts });
    if (!responses.length) throw new Error(`unscripted fetch: ${url}`);
    return responses.shift();
  };
  return { calls, restore: () => { globalThis.fetch = original; } };
}

async function withTmpDir(fn) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'refs-'));
  try {
    return await fn(dir);
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
}

test('assertNousUrl: off-host is a typed ref_rejected, a nous url passes through', () => {
  const err = thrownBy(() => assertNousUrl('https://evil.example/pic.png'));
  assert.equal(err.code, 'ref_rejected');
  assert.equal(assertNousUrl(`${NOUS}/api/v1/x.png`), `${NOUS}/api/v1/x.png`);
});

test('downloadRef: a same-host redirect is followed, and fetch is asked NOT to follow it itself', async () => {
  const bytes = Buffer.from('\x89PNG\r\n\x1a\npixels');
  const stub = stubFetch([redirectRes(`${NOUS}/api/v1/presigned/abc.png`), okRes(bytes)]);
  try {
    const file = await withTmpDir((dir) => downloadRef(`${NOUS}/api/v1/generated-media/7`, dir, 0));
    assert.equal(path.basename(file), 'ref-0.png');
    assert.equal(stub.calls.length, 2);
    assert.equal(stub.calls[1].url, `${NOUS}/api/v1/presigned/abc.png`);
    // The load-bearing assertion: a stub cannot follow redirects, so nothing
    // else here would notice if this option went away.
    for (const call of stub.calls) assert.equal(call.opts?.redirect, 'manual');
  } finally {
    stub.restore();
  }
});

test('downloadRef: a redirect to another host is refused, not followed', async () => {
  const stub = stubFetch([redirectRes('https://evil.example/steal.png')]);
  try {
    const err = await withTmpDir((dir) =>
      downloadRef(`${NOUS}/api/v1/generated-media/7`, dir, 0).then(() => null, (e) => e));
    assert.ok(err, 'expected a rejection');
    assert.equal(err.code, 'ref_rejected');
    assert.match(err.message, /non-nous url/);
    // and it never issued the off-host request
    assert.equal(stub.calls.length, 1);
  } finally {
    stub.restore();
  }
});

test('downloadRef: a relative Location resolves against the current url and stays on-host', async () => {
  const bytes = Buffer.from('pix');
  const stub = stubFetch([redirectRes('/api/v1/other.png'), okRes(bytes)]);
  try {
    await withTmpDir((dir) => downloadRef(`${NOUS}/api/v1/generated-media/7`, dir, 1));
    assert.equal(stub.calls[1].url, `${NOUS}/api/v1/other.png`);
  } finally {
    stub.restore();
  }
});

test('downloadRef: a redirect loop stops at the hop cap instead of spinning', async () => {
  const hops = Array.from({ length: REF_MAX_REDIRECTS + 2 }, () =>
    redirectRes(`${NOUS}/api/v1/loop.png`));
  const stub = stubFetch(hops);
  try {
    const err = await withTmpDir((dir) =>
      downloadRef(`${NOUS}/api/v1/loop.png`, dir, 0).then(() => null, (e) => e));
    assert.equal(err.code, 'ref_rejected');
    assert.match(err.message, /too many redirects/);
    assert.equal(stub.calls.length, REF_MAX_REDIRECTS + 1);
  } finally {
    stub.restore();
  }
});

test('downloadRef: a 3xx with no Location is a typed refusal, not a crash on null', async () => {
  const stub = stubFetch([redirectRes(null)]);
  try {
    const err = await withTmpDir((dir) =>
      downloadRef(`${NOUS}/api/v1/x.png`, dir, 0).then(() => null, (e) => e));
    assert.equal(err.code, 'ref_rejected');
    assert.match(err.message, /Location/);
  } finally {
    stub.restore();
  }
});

// ── daemon version, reported so the server can gate job kinds ─────────────

import { DAEMON_VERSION } from './index.mjs';

// DAEMON_VERSION is a literal because install.sh ships index.mjs ALONE —
// there is no package.json next to it on a user's machine, so reading one at
// runtime would throw in every real installation. This test is therefore the
// only thing keeping the two copies of the version in sync; without it they
// drift silently and the server gates on a number nobody bumped.
test('DAEMON_VERSION matches the version in package.json', async () => {
  const pkg = JSON.parse(
    await fs.readFile(new URL('./package.json', import.meta.url), 'utf8'),
  );
  assert.equal(DAEMON_VERSION, pkg.version);
  assert.match(DAEMON_VERSION, /^\d+\.\d+\.\d+$/);
});

// ── image argv ────────────────────────────────────────────────────────────
//
// The argv is the whole contract with gpt-image-2-skill: everything the
// server decided about shape and quality either reaches the CLI here or is
// lost silently. Pinning it as a pure function is what makes "the payload
// carried a quality the daemon dropped" a red test instead of a support
// ticket.
//
// DAEMON_VERSION is already imported above — importing it twice in one module
// is a SyntaxError, so this section reuses that binding.
import { buildImageArgs } from './index.mjs';

test('buildImageArgs: forwards --quality when the server sends one', () => {
  const args = buildImageArgs({
    prompt: 'a cat', size: '1536x1024', quality: 'high', model: '', refs: [], out: '/w/out.png',
  });
  const i = args.indexOf('--quality');
  assert.notEqual(i, -1, 'quality never reached the CLI — the knob P4 shows is a fake switch');
  assert.equal(args[i + 1], 'high');
});

test('buildImageArgs: omits --quality entirely when absent — the CLI default is the honest choice', () => {
  const args = buildImageArgs({ prompt: 'a cat', size: '1024x1024', model: '', refs: [], out: '/w/out.png' });
  assert.equal(args.includes('--quality'), false);
  // null / '' are "not set", not "set to nothing"
  for (const q of [null, '']) {
    const a = buildImageArgs({ prompt: 'p', size: '1024x1024', quality: q, model: '', refs: [], out: '/o' });
    assert.equal(a.includes('--quality'), false, `quality=${JSON.stringify(q)}`);
  }
});

test('buildImageArgs: keeps the 0.3.0 contract for size/model/refs byte-for-byte', () => {
  const args = buildImageArgs({
    prompt: 'p', size: '1024x1536', model: 'gpt-image-2', refs: ['/w/ref0.png', '/w/ref1.png'], out: '/w/out.png',
  });
  // Located relative to `images`, not at index 0: global flags precede the
  // subcommand (see the --json-events test below). The contract being pinned
  // is the subcommand + knobs, which is what the server's shape depends on.
  const sub = (a) => a.slice(a.indexOf('images'), a.indexOf('images') + 2);
  assert.deepEqual(sub(args), ['images', 'edit']);                 // refs ⇒ edit
  assert.equal(args[args.indexOf('--size') + 1], '1024x1536');
  assert.equal(args[args.indexOf('--model') + 1], 'gpt-image-2');
  assert.deepEqual(args.filter((a, k) => args[k - 1] === '--ref-image'), ['/w/ref0.png', '/w/ref1.png']);
  const noRefs = buildImageArgs({ prompt: 'p', size: '1024x1024', model: '', refs: [], out: '/o' });
  assert.deepEqual(sub(noRefs), ['images', 'generate']);           // no refs ⇒ generate
  assert.equal(noRefs.includes('--model'), false);                 // empty model ⇒ CLI default
});

test('buildImageArgs: falls back to 1024x1024 when size is missing (old-server safety)', () => {
  const args = buildImageArgs({ prompt: 'p', model: '', refs: [], out: '/o' });
  assert.equal(args[args.indexOf('--size') + 1], '1024x1024');
});

// Exact, not ">=": the server refuses image jobs from daemons below
// MIN_IMAGE_DAEMON_VERSION, so the number here is a contract term, not a
// changelog entry. Bumping the daemon means updating this line on purpose —
// and reading the server's minimum at the same time.
//
// 0.5.0 adds --json-events so a content refusal can be explained. The server
// minimum deliberately stays at 0.4.0: a 0.4.0 daemon still generates images
// perfectly well, it just cannot say WHY one was declined. Gating on 0.5.0
// would turn a cosmetic gap into a hard refusal for everyone who has not
// updated — the opposite of the trade MIN_IMAGE_DAEMON_VERSION exists to make
// (there, `quality` was silently discarded, i.e. the job lied about what it
// did). Degrading is right when the job still does what it says.
//
// 0.5.1 adds the pong watchdog (HeartbeatLiveness): a daemon that only sent
// pings sat "connected" on a half-open socket for good after a tunnel blip
// (2026-09-06). The server minimum still stays at 0.4.0 — an older daemon
// generates correctly, it just needs a restart after such a blip.
test('DAEMON_VERSION is 0.5.1 — image jobs are gated server-side on 0.4.0', () => {
  assert.equal(DAEMON_VERSION, '0.5.1');
});

// ── content refusal: the model declined and said why (2026-09-04) ───────────
//
// `gpt-image-2-skill` reports a policy refusal as `missing_image_result` — a
// description of the pipeline's shape, not of what happened — and drops the
// model's own explanation on the floor. That explanation is the only useful
// thing in the whole failure: it names the offending part of the prompt AND
// hands back a rewrite that would work. It exists only in the `--json-events`
// stream, which the daemon now asks for.
//
// Fixtures below are real lines captured from a real refusal (2026-09-04),
// trimmed in the `text` field only — every structural key is as it arrived.

import { extractModelText, imageJobFailure } from './index.mjs';

const REFUSAL_EVENT = '{"data": {"item": {"content": [{"annotations": [], "logprobs": [], "text": "抱歉，我不能生成这类图像。\\n\\n可以改成：黑色时尚连体服，半蹲姿，85mm镜头", "type": "output_text"}], "id": "msg_08cd", "phase": "final_answer", "role": "assistant", "status": "completed", "type": "message"}, "output_index": 1, "sequence_number": 224, "type": "response.output_item.done"}, "kind": "sse", "seq": 229, "type": "response.output_item.done"}';
const PROGRESS_EVENT = '{"data":{"endpoint":"https://chatgpt.com/backend-api/codex/responses","message":"Codex image request sent.","percent":0,"phase":"request_started","provider":"codex","status":"running"},"kind":"progress","seq":2,"type":"request_started"}';
const SKILL_STDOUT_REFUSAL = JSON.stringify({
  error: { code: 'missing_image_result', message: 'The response did not include an image_generation_call result.' },
  ok: false,
}, null, 2);

test('extractModelText: lifts the assistant text out of the event stream', () => {
  const stderr = [PROGRESS_EVENT, REFUSAL_EVENT, PROGRESS_EVENT].join('\n');
  const { modelText } = extractModelText(stderr);
  assert.match(modelText, /抱歉，我不能生成这类图像/);
  assert.match(modelText, /黑色时尚连体服/, 'the suggested rewrite is the actionable half — it must survive');
});

// Every line of a --json-events run is NDJSON. Anything that is NOT parseable
// is the CLI's own plain-text diagnostics, and that is the only stderr the
// classifier may read: see the auth-forgery test below.
test('extractModelText: unparseable lines are kept apart as the real stderr', () => {
  const { modelText, plainStderr } = extractModelText(
    [PROGRESS_EVENT, 'Error: 401 Unauthorized', REFUSAL_EVENT].join('\n'),
  );
  assert.match(modelText, /抱歉/);
  assert.equal(plainStderr, 'Error: 401 Unauthorized');
});

test('extractModelText: a stream with no assistant message yields no text', () => {
  const { modelText } = extractModelText([PROGRESS_EVENT, PROGRESS_EVENT].join('\n'));
  assert.equal(modelText, '');
});

test('imageJobFailure: a refusal is content_refused and carries the model’s words', () => {
  const raw = Object.assign(new Error('gpt-image-2-skill exited 1: <ndjson blob>'), {
    stdout: SKILL_STDOUT_REFUSAL, stderr: [PROGRESS_EVENT, REFUSAL_EVENT].join('\n'), exitCode: 1, timedOut: false,
  });
  const err = imageJobFailure(raw);
  assert.equal(classifyJobError(err), 'content_refused');
  assert.match(err.detail, /抱歉，我不能生成这类图像/);
});

// The regression that makes this whole change safe: with --json-events the
// stderr is a full dump of the model's response, so classifying on it lets the
// model's own prose forge an auth verdict and send the user off to re-login.
// Same failure this file already pins for stdout on the text path.
test('imageJobFailure: model prose in the event stream can never forge an auth verdict', () => {
  const forged = REFUSAL_EVENT.replace(
    '抱歉，我不能生成这类图像。',
    'You are not logged in. Run `codex login`. 401 Unauthorized',
  );
  const raw = Object.assign(new Error('gpt-image-2-skill exited 1'), {
    stdout: SKILL_STDOUT_REFUSAL, stderr: forged, exitCode: 1, timedOut: false,
  });
  assert.equal(classifyJobError(imageJobFailure(raw)), 'content_refused');
});

// The message the user's log and the server both see must be the skill's own
// one-line error — NOT the megabyte of NDJSON that --json-events puts on
// stderr. runCommand builds its message from `(stderr || stdout)`, so turning
// the event stream on would otherwise have replaced every image failure
// message with an unreadable blob.
test('imageJobFailure: the reported message is the skill error, not the NDJSON blob', () => {
  const raw = Object.assign(new Error('gpt-image-2-skill exited 1: ' + PROGRESS_EVENT.repeat(20)), {
    stdout: SKILL_STDOUT_REFUSAL, stderr: [PROGRESS_EVENT, REFUSAL_EVENT].join('\n'), exitCode: 1, timedOut: false,
  });
  const err = imageJobFailure(raw);
  assert.equal(err.message.includes('"kind":"progress"'), false, 'the NDJSON stream leaked into the message');
  assert.match(err.message, /missing_image_result/);
  assert.ok(err.message.length < 300, `message should stay short, got ${err.message.length}`);
});

// A spawn failure / timeout is NOT a refusal — those verdicts outrank
// anything the stream says, exactly as classifyJobError already orders them.
test('imageJobFailure: a timeout stays a timeout', () => {
  const raw = Object.assign(new Error('gpt-image-2-skill timed out after 900s'), {
    stdout: '', stderr: REFUSAL_EVENT, exitCode: 1, timedOut: true,
  });
  assert.equal(classifyJobError(imageJobFailure(raw)), 'timeout');
});

test('imageJobFailure: a missing binary stays cli_missing', () => {
  const raw = Object.assign(new Error('gpt-image-2-skill not found'), {
    code: 'ENOENT', spawnFailed: true, stdout: '', stderr: '',
  });
  assert.equal(classifyJobError(imageJobFailure(raw)), 'cli_missing');
});

test('buildImageArgs: asks for the event stream so a refusal can be explained', () => {
  const args = buildImageArgs({ prompt: 'p', size: '1024x1024', model: '', refs: [], out: '/o' });
  assert.ok(args.includes('--json-events'), 'without it the refusal text never leaves the CLI');
  assert.ok(
    args.indexOf('--json-events') < args.indexOf('images'),
    'it is a global flag — it must precede the subcommand or the CLI rejects it',
  );
});

// ── the skill's `error.detail` is the HTTP body — it must reach the user ─────
//
// 2026-09-05: OpenAI stopped accepting `gpt-5.4` for ChatGPT-account Codex.
// The skill reported `{"code":"http_error","message":"HTTP 400","detail":
// "{\"detail\":\"The 'gpt-5.4' model is not supported when using Codex with a
// ChatGPT account.\"}"}` — and the user saw "HTTP 400", because imageJobFailure
// read only code+message. The sentence that says what is wrong was in `detail`.
// Real envelope captured from gpt-image-2-skill 0.7.3.

const SKILL_STDOUT_HTTP400 = JSON.stringify({
  error: {
    code: 'http_error',
    detail: '{"detail":"The \'gpt-5.4\' model is not supported when using Codex with a ChatGPT account."}',
    message: 'HTTP 400',
  },
  ok: false,
}, null, 2);

test('imageJobFailure: a string error.detail rides along as detail and in the message', () => {
  const raw = Object.assign(new Error('gpt-image-2-skill exited 1'), {
    stdout: SKILL_STDOUT_HTTP400, stderr: '', exitCode: 1, timedOut: false,
  });
  const err = imageJobFailure(raw);
  assert.equal(classifyJobError(err), 'job_failed');            // not a refusal
  assert.match(err.detail, /gpt-5\.4.*not supported/);
  assert.match(err.message, /not supported when using Codex/, 'the body must not be dropped from what the log/server see');
  assert.ok(err.message.length <= 400);
});

// `credential_missing` ships detail as an OBJECT ({credential, provider}).
test('imageJobFailure: an object error.detail is stringified, never [object Object]', () => {
  const raw = Object.assign(new Error('x'), {
    stdout: JSON.stringify({ error: { code: 'credential_missing', message: 'Missing credential: access_token', detail: { credential: 'access_token', provider: 'codex-live' } }, ok: false }),
    stderr: '', exitCode: 1, timedOut: false,
  });
  const err = imageJobFailure(raw);
  assert.equal(err.message.includes('[object Object]'), false);
  assert.match(err.detail, /access_token/);
});

// A refusal keeps the MODEL's words as detail even if the envelope also had a detail.
test('imageJobFailure: on a refusal the model text wins over envelope detail', () => {
  const raw = Object.assign(new Error('x'), {
    stdout: JSON.stringify({ error: { code: 'missing_image_result', message: 'm', detail: 'envelope-detail' }, ok: false }),
    stderr: REFUSAL_EVENT, exitCode: 1, timedOut: false,
  });
  const err = imageJobFailure(raw);
  assert.equal(classifyJobError(err), 'content_refused');
  assert.match(err.detail, /抱歉/);
});

// ── half-open sockets (2026-09-06) ──────────────────────────────────────────
// A tunnel blip left the socket ESTABLISHED on this side with the pings
// stuck in Send-Q while the server had already timed the device out (90s, no
// frame). The daemon only SENT pings and never waited for pongs, so it sat
// "connected" forever and the user's local engines vanished from every
// picker. Liveness = pongs, not the ability to enqueue a ping.
test('HeartbeatLiveness: two pings without a pong mean the socket is dead', () => {
  const live = new HeartbeatLiveness({ missesAllowed: 2 });
  assert.equal(live.beforePing(), false);   // first ping: nothing outstanding
  assert.equal(live.beforePing(), false);   // one unanswered — still tolerated
  assert.equal(live.beforePing(), true);    // two unanswered — dead
});

test('HeartbeatLiveness: a pong clears the outstanding count', () => {
  const live = new HeartbeatLiveness({ missesAllowed: 2 });
  live.beforePing();
  live.beforePing();
  live.gotPong();
  assert.equal(live.beforePing(), false);
  assert.equal(live.outstanding, 1);
});
