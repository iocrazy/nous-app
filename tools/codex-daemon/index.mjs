#!/usr/bin/env node
/**
 * nous-codex — run nous canvas generations on YOUR machine with YOUR codex
 * login (C 方案 daemon; design:
 * docs/superpowers/specs/2026-08-23-codex-per-user-daemon-design.md).
 *
 * Your codex credentials NEVER leave this machine. nous only sends job
 * descriptions over an outbound WebSocket; this process runs the CLI locally
 * and uploads the resulting file back.
 *
 *   nous-codex pair <CODE> [--name <device>]  pair this machine with nous
 *   nous-codex run                            stay connected and take jobs
 *   nous-codex install-service                run at login/boot, auto-restart
 *   nous-codex uninstall-service              remove that service
 *   nous-codex status                         config / service / CLI report
 *
 * Safety: only three commands are ever executed — `codex exec`,
 * `gpt-image-2-skill images` and `dreamina <whitelisted subcommand>` — with
 * argv arrays, never a shell string. The server cannot make this process run
 * anything else.
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const API_BASE = process.env.NOUS_API_BASE || 'https://api.nous.ink';
const WS_BASE = API_BASE.replace(/^http/, 'ws');
/** Both the config dir and the systemd unit dir must agree on where
 *  "$XDG_CONFIG_HOME" is, or `install-service` writes the unit somewhere the
 *  config is not — which is exactly how a service ends up running as "not
 *  paired yet" while `status` from a shell looks fine. Unset means ~/.config
 *  per the XDG spec, so this is a no-op for almost everyone. */
function xdgConfigHome() {
  return process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
}

const CONFIG_DIR = path.join(xdgConfigHome(), 'nous-codex');
const CONFIG_FILE = path.join(CONFIG_DIR, 'config.json');
const HEARTBEAT_MS = 30_000;
const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

/** Written next to config.json when the daemon stops for good, so `status`
 *  can say WHY a service that is no longer running stopped. Needed because
 *  the terminal exit is now a clean exit 0 (see run()) — without this the
 *  only trace would be a line in the journal. */
const LAST_STOP_FILENAME = 'last_stop.json';

const SERVICE_LABEL = 'ink.nous.codex';
const SYSTEMD_UNIT_NAME = 'nous-codex.service';
const WIN_TASK_NAME = 'NousCodex';

const log = (...a) => console.log(new Date().toISOString(), ...a);

// ── config ────────────────────────────────────────────────────────────────

async function readConfig() {
  try {
    return JSON.parse(await fs.readFile(CONFIG_FILE, 'utf8'));
  } catch {
    return null;
  }
}

async function writeConfig(cfg) {
  await fs.mkdir(CONFIG_DIR, { recursive: true, mode: 0o700 });
  await fs.writeFile(CONFIG_FILE, JSON.stringify(cfg, null, 2), { mode: 0o600 });
}

// ── last stop ─────────────────────────────────────────────────────────────
//
// The daemon exits 0 when it is revoked, so that every service manager (which
// all leave a clean exit alone) stops it for good with one code. The cost of a
// clean exit is that nothing about it looks unusual afterwards — so the reason
// is recorded here and surfaced by `status`.

/** Terminal close codes → the reason recorded in last_stop.json.
 *  Returns null for every retryable close. */
export function stopReasonForCloseCode(code) {
  if (code === 4003) return 'revoked';
  if (code === 4001) return 'auth_failed';
  return null;
}

// One sentence per reason, carrying the fix. Used verbatim by both the
// console line at exit and by `status`, so the two can never drift into
// telling the user different things about the same event.
const STOP_REASON_TEXT = {
  revoked: 'this device was revoked in nous — re-pair from Settings → AI → Local CLI to use it again',
  auth_failed:
    'this device token is no longer valid — re-pair from Settings → AI → Local CLI to use it again',
};

/** One line for `status`, or null when there is nothing (or nothing we
 *  recognise) to report — an unknown reason must not print as if understood. */
export function describeLastStop(entry) {
  if (!entry || typeof entry !== 'object') return null;
  const text = STOP_REASON_TEXT[entry.reason];
  if (!text) return null;
  return `${text} (stopped ${entry.at || 'at an unknown time'})`;
}

export async function writeLastStop(reason, dir = CONFIG_DIR) {
  await fs.mkdir(dir, { recursive: true, mode: 0o700 });
  await fs.writeFile(
    path.join(dir, LAST_STOP_FILENAME),
    JSON.stringify({ reason, at: new Date().toISOString() }, null, 2),
    { mode: 0o600 },
  );
}

export async function readLastStop(dir = CONFIG_DIR) {
  try {
    return JSON.parse(await fs.readFile(path.join(dir, LAST_STOP_FILENAME), 'utf8'));
  } catch {
    return null;
  }
}

export async function clearLastStop(dir = CONFIG_DIR) {
  await fs.rm(path.join(dir, LAST_STOP_FILENAME), { force: true });
}

// ── argv ──────────────────────────────────────────────────────────────────

/** `pair ABCD2345 --name studio-mac` → { code: 'ABCD2345', name: 'studio-mac' }.
 *  Pure, so flag handling is testable without touching the network. */
export function parsePairArgs(argv) {
  let code = null;
  let name = null;
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--name') {
      name = argv[i + 1] ?? null;
      i += 1;
    } else if (a.startsWith('--name=')) {
      name = a.slice('--name='.length);
    } else if (!a.startsWith('-') && code === null) {
      code = a;
    }
  }
  if (name !== null) name = name.trim();
  return { code, name: name || null };
}

// ── pairing ───────────────────────────────────────────────────────────────

async function pair(argv) {
  const { code, name } = parsePairArgs(argv);
  if (!code) throw new Error('usage: nous-codex pair <CODE> [--name <device>]');
  const deviceName = (name || os.hostname()).slice(0, 64);
  const res = await fetch(`${API_BASE}/api/v1/codex-daemon/pair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code: code.trim().toUpperCase(),
      device_name: deviceName,
      platform: process.platform,
    }),
  });
  if (!res.ok) {
    throw new Error(`pairing failed (${res.status}): ${(await res.text()).slice(0, 200)}`);
  }
  const { data } = await res.json();
  await writeConfig({ device_id: data.device_id, device_token: data.device_token });
  // A fresh pairing answers whatever the last stop was; leaving the file
  // would make `status` keep reporting a revocation that no longer applies.
  await clearLastStop();
  const self = path.basename(scriptPath());
  log(`paired as "${deviceName}" (device ${data.device_id})`);
  log(`next: node ${self} install-service   — start at login and restart on crash`);
  log(`  or: node ${self} run               — stay in the foreground to try it first`);
}

// ── job execution (WHITELIST — the only commands this process ever runs) ──

/** Failure signals travel as fields, never as prose to be re-parsed.
 *  `message` still falls back to stdout so a human reading the log sees
 *  something, but classifyJobError reads `stderr` / `code` / `exitCode` —
 *  stdout is the model's own output and must never steer a verdict. */
function failure(message, fields) {
  return Object.assign(new Error(message), {
    stderr: '', exitCode: null, timedOut: false, ...fields,
  });
}

export function runCommand(bin, args, { timeoutMs = 15 * 60_000, stdin = null } = {}) {
  return new Promise((resolve, reject) => {
    // argv array, never a shell string: nothing the server sends can be
    // interpreted as shell syntax.
    const child = spawn(bin, args, { stdio: [stdin == null ? 'ignore' : 'pipe', 'pipe', 'pipe'] });
    let out = '';
    let err = '';
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(failure(`${bin} timed out after ${Math.round(timeoutMs / 1000)}s`, {
        stderr: err, timedOut: true,
      }));
    }, timeoutMs);
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    child.on('error', (e) => {
      clearTimeout(timer);
      // e.code is ENOENT (absent) / EACCES (present but not runnable). Keep it
      // for diagnostics, but classify on `spawnFailed`: a bare ENOENT also
      // comes from fs.readFile elsewhere, and "the CLI is missing" is the
      // wrong thing to tell a user whose CLI ran fine but wrote no file.
      reject(failure(`${bin} not found or not executable: ${e.message}`, {
        code: e.code, spawnFailed: true, stderr: err,
      }));
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve({ out, err });
      else {
        reject(failure(`${bin} exited ${code}: ${(err || out).slice(0, 400)}`, {
          stderr: err, exitCode: code,
        }));
      }
    });
    if (stdin != null) {
      child.stdin.on('error', () => { /* EPIPE when codex exits early — the close handler reports it */ });
      child.stdin.end(stdin);
    }
  });
}

/** Run a local admin command (systemctl / launchctl / schtasks) and report
 *  the outcome instead of throwing — `status` must survive a missing tool. */
function tryCommand(bin, args) {
  return new Promise((resolve) => {
    const child = spawn(bin, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    let err = '';
    child.stdout?.on('data', (d) => (out += d));
    child.stderr?.on('data', (d) => (err += d));
    child.on('error', (e) => resolve({ ok: false, code: null, out: '', err: e.message }));
    child.on('close', (code) => resolve({ ok: code === 0, code, out, err }));
  });
}

async function requireCommand(bin, args) {
  const r = await tryCommand(bin, args);
  if (!r.ok) {
    const detail = (r.err || r.out || `exit ${r.code}`).trim().slice(0, 300);
    throw new Error(`${bin} ${args.join(' ')} failed: ${detail}`);
  }
  return r;
}

async function downloadRef(url, dir, index) {
  // SSRF guard: only nous' own API may be fetched by this daemon.
  if (!url.startsWith(`${API_BASE}/`)) {
    throw new Error(`refusing to fetch a non-nous url: ${url.slice(0, 80)}`);
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`ref download failed (${res.status})`);
  const file = path.join(dir, `ref-${index}.png`);
  await fs.writeFile(file, Buffer.from(await res.arrayBuffer()));
  return file;
}

async function runImageJob(payload, workDir) {
  const out = path.join(workDir, 'out.png');
  const refs = [];
  for (const [i, url] of (payload.ref_urls ?? []).slice(0, 9).entries()) {
    refs.push(await downloadRef(url, workDir, i));
  }
  const args = [
    'images',
    refs.length ? 'edit' : 'generate',
    '--prompt', String(payload.prompt ?? ''),
    '--out', out,
    '--size', String(payload.size || '1024x1024'),
    '--format', 'png',
    '--background', 'opaque',
  ];
  if (payload.model) args.push('--model', String(payload.model));
  for (const ref of refs) args.push('--ref-image', ref);
  await runCommand('gpt-image-2-skill', args);
  return out;
}

const DREAMINA_COMMANDS = new Set([
  'text2image', 'image_upscale', 'text2video', 'image2video',
  'multimodal2video', 'frames2video',
]);
const MEDIA_EXTS = ['.png', '.jpg', '.jpeg', '.webp', '.mp4', '.mov', '.webm'];

/** Run one dreamina generation on the user's own login. The server sends a
 *  pre-built argv (same pure builders it uses itself); this side only
 *  whitelists the subcommand, swaps {ref:N} placeholders for downloaded
 *  files, and never touches a shell. */
async function runDreaminaJob(payload, workDir) {
  const raw = Array.isArray(payload.submit_args) ? payload.submit_args.map(String) : [];
  if (!raw.length || !DREAMINA_COMMANDS.has(raw[0])) {
    throw new Error(`refused dreamina subcommand: ${raw[0] ?? '(none)'}`);
  }
  for (const a of raw.slice(1)) {
    if (!a.startsWith('--')) throw new Error(`refused dreamina arg: ${a.slice(0, 40)}`);
  }
  const refs = [];
  for (const [i, url] of (payload.ref_urls ?? []).slice(0, 9).entries()) {
    refs.push(await downloadRef(url, workDir, i));
  }
  const args = raw.map((a) => a.replace(/\{ref:(\d+)\}/g, (_, n) => refs[Number(n)] ?? ''));

  const { out } = await runCommand('dreamina', args, { timeoutMs: 20 * 60_000 });
  const jsonStart = out.indexOf('{');
  let submitId = null;
  if (jsonStart >= 0) {
    try { submitId = JSON.parse(out.slice(jsonStart)).submit_id ?? null; } catch { /* scan below */ }
  }
  if (submitId) {
    await runCommand('dreamina', [
      'query_result', `--submit_id=${submitId}`, `--download_dir=${workDir}`,
    ], { timeoutMs: 5 * 60_000 });
  }
  const files = await fs.readdir(workDir);
  const media = files.find((f) => MEDIA_EXTS.includes(path.extname(f).toLowerCase()) && !f.startsWith('ref-'));
  if (!media) throw new Error('dreamina produced no media file');
  return path.join(workDir, media);
}

export const TEXT_INLINE_LIMIT = 900 * 1024; // uvicorn's default WS frame cap is 1 MiB
export const CHUNK_BYTES = 256 * 1024;
const TEXT_TIMEOUT_DEFAULT_MS = 180_000;
const TEXT_TIMEOUT_MAX_MS = 600_000;

/** Fixed sandbox: read-only, ephemeral, cwd = an empty temp dir. IC ran
 *  `--sandbox workspace-write --cd <app dir>` and then asked the model in the
 *  prompt not to write files — we do not repeat that. */
export function buildCodexExecArgs({ model, imagePaths, workDir }) {
  const args = ['exec', '--json', '--ephemeral', '--skip-git-repo-check', '-s', 'read-only', '-C', workDir];
  if (model) args.push('--model', String(model));
  for (const p of imagePaths ?? []) args.push('--image', p);
  args.push('-'); // prompt from stdin: long prompts never hit argv / ps
  return args;
}

/** `codex exec --json` prints JSONL. The answer is the LAST
 *  item.completed whose item.type is agent_message; turn.completed.usage
 *  carries real token counts. Non-JSON lines are ignored. */
export function parseCodexExecOutput(jsonl) {
  let text = null;
  let usage = {};
  let threadId = null;
  for (const line of String(jsonl).split('\n')) {
    let ev;
    try { ev = JSON.parse(line); } catch { continue; }
    if (ev?.type === 'thread.started' && ev.thread_id) threadId = ev.thread_id;
    if (ev?.type === 'item.completed' && ev.item?.type === 'agent_message' && typeof ev.item.text === 'string') {
      text = ev.item.text;
    }
    if (ev?.type === 'turn.completed' && ev.usage && typeof ev.usage === 'object') usage = ev.usage;
  }
  if (text == null) {
    throw Object.assign(
      new Error('codex_no_output: codex exec finished without an agent_message'),
      { code: 'codex_no_output' },
    );
  }
  return { text, usage, threadId };
}

export function chunkText(text, size = CHUNK_BYTES) {
  const buf = Buffer.from(text, 'utf8');
  const parts = [];
  let start = 0;
  while (start < buf.length) {
    let end = Math.min(start + size, buf.length);
    // never cut inside a UTF-8 sequence: back up to a char boundary
    while (end < buf.length && (buf[end] & 0xc0) === 0x80) end -= 1;
    parts.push(buf.subarray(start, end).toString('utf8'));
    start = end;
  }
  return parts;
}

/** Text jobs carry refs under `image_urls`; the image/dreamina jobs next door
 *  use `ref_urls`. A payload that gets that wrong, or ships a non-string
 *  array, used to fall through `Array.isArray(...) ? ... : []` and produce an
 *  empty list — the model then answered confidently about images it never
 *  saw. Every rejection is typed and loud instead. */
export function normalizeImageUrls(payload) {
  const refuse = (why) => {
    throw Object.assign(new Error(`ref_rejected: ${why}`), { code: 'ref_rejected' });
  };
  const raw = payload?.image_urls;
  if (raw == null) {
    if (payload?.ref_urls != null) refuse('refs must be sent as image_urls, not ref_urls');
    return [];
  }
  if (!Array.isArray(raw) || raw.some((u) => typeof u !== 'string')) {
    refuse('image_urls must be an array of strings');
  }
  return raw.slice(0, 9);
}

/** The wire contract Task 2 reassembles. Kept as a pure function over `send`
 *  so both branches are testable: the chunked one is otherwise near-dead code
 *  that would first execute in production. */
export function emitTextResult(send, { text, usage }) {
  if (Buffer.byteLength(text, 'utf8') <= TEXT_INLINE_LIMIT) {
    send({ type: 'job_done', text, usage, chunked: false });
    return;
  }
  const parts = chunkText(text);
  parts.forEach((data, seq) => send({ type: 'job_chunk', seq, data }));
  send({ type: 'job_done', usage, chunked: true, chunks: parts.length });
}

/** Auth signals only ever appear on stderr. Deliberately narrow: a bare
 *  `login` substring matched `systemd-logind` and any doc URL. */
const AUTH_RE = /\bnot logged in\b|\bplease log ?in\b|\b401\b|unauthori[sz]ed/i;

/** Map a thrown error to a wire `code`, using structured fields only — the
 *  Error message contains codex stdout, i.e. text the model controls. */
export function classifyJobError(err) {
  if (err?.spawnFailed) return 'cli_missing';
  // Ahead of exit code and stderr: SIGKILL races the child's own exit, so a
  // timed-out run can still carry both, and neither is the real story.
  if (err?.timedOut) return 'timeout';
  const thrown = err?.code;
  if (thrown === 'codex_no_output' || thrown === 'ref_rejected') return thrown;
  if (AUTH_RE.test(String(err?.stderr ?? ''))) return 'codex_not_logged_in';
  return 'job_failed';
}

async function runTextJob(payload, workDir) {
  const refs = normalizeImageUrls(payload);
  const imagePaths = [];
  for (let i = 0; i < refs.length; i += 1) imagePaths.push(await downloadRef(refs[i], workDir, i));
  const args = buildCodexExecArgs({ model: payload.model, imagePaths, workDir });
  const timeoutMs = Math.min(
    Number(payload.timeout_s) > 0 ? Number(payload.timeout_s) * 1000 : TEXT_TIMEOUT_DEFAULT_MS,
    TEXT_TIMEOUT_MAX_MS,
  );
  const { out } = await runCommand('codex', args, { timeoutMs, stdin: String(payload.prompt ?? '') });
  return parseCodexExecOutput(out);
}

const MIME_BY_EXT = {
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.webp': 'image/webp', '.mp4': 'video/mp4', '.mov': 'video/quicktime',
  '.webm': 'video/webm',
};

async function uploadResult(filePath, ticket) {
  const body = new FormData();
  const bytes = await fs.readFile(filePath);
  const mime = MIME_BY_EXT[path.extname(filePath).toLowerCase()] ?? 'image/png';
  body.append('file', new Blob([bytes], { type: mime }), path.basename(filePath));
  body.append('ticket', ticket);
  const res = await fetch(`${API_BASE}/api/v1/codex-daemon/upload`, {
    method: 'POST',
    body,
  });
  if (!res.ok) throw new Error(`upload failed (${res.status})`);
  const { data } = await res.json();
  return data.gen_id;
}

// ── connection ────────────────────────────────────────────────────────────

/** WebSocket close codes that mean "stop trying".
 *    4001 — device authentication failed (codex_daemon_ws_router.py)
 *    4003 — device revoked               (services/codex/daemon_registry.py)
 *  Everything else (network blips, server restarts, 1006) is retryable.
 *  Retrying a revoked token forever is exactly the silently-useless daemon
 *  this guards against. Derived from stopReasonForCloseCode so the two can
 *  never disagree about which closes are final. */
export function isTerminalClose(code) {
  return stopReasonForCloseCode(code) !== null;
}

async function connect(cfg) {
  const ws = new WebSocket(`${WS_BASE}/api/v1/ws/codex-agent`, {
    headers: { Authorization: `Bearer ${cfg.device_token}` },
  });
  let heartbeat;

  ws.addEventListener('open', () => {
    log('connected to nous');
    // Whatever stopped us last time is demonstrably over — otherwise `status`
    // keeps reporting a revocation that a later re-pair already resolved.
    clearLastStop().catch(() => {});
    if (cfg.envReport) {
      try {
        ws.send(JSON.stringify({ type: 'env_report', report: cfg.envReport }));
      } catch { /* non-fatal */ }
    }
    heartbeat = setInterval(() => {
      try {
        ws.send(JSON.stringify({ type: 'ping' }));
      } catch {
        /* the close handler will reconnect */
      }
    }, HEARTBEAT_MS);
  });

  ws.addEventListener('message', async (event) => {
    let msg;
    try {
      msg = JSON.parse(String(event.data));
    } catch {
      return;
    }
    if (msg.type !== 'job') return;
    const jobId = msg.job_id;
    const workDir = await fs.mkdtemp(path.join(os.tmpdir(), 'nous-codex-'));
    const send = (payload) => ws.send(JSON.stringify({ ...payload, job_id: jobId }));
    try {
      send({ type: 'job_progress', phase: 'running' });
      if (msg.payload?.engine === 'dreamina') {
        const file = await runDreaminaJob(msg.payload ?? {}, workDir);
        const genId = await uploadResult(file, msg.payload?.upload_ticket);
        send({ type: 'job_done', gen_id: String(genId) });
      } else if (msg.kind === 'image') {
        const file = await runImageJob(msg.payload ?? {}, workDir);
        const genId = await uploadResult(file, msg.payload?.upload_ticket);
        send({ type: 'job_done', gen_id: String(genId) });
      } else if (msg.kind === 'text') {
        emitTextResult(send, await runTextJob(msg.payload ?? {}, workDir));
      } else {
        send({ type: 'job_failed', code: 'unsupported_kind', message: String(msg.kind) });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const code = classifyJobError(err);
      log(`job ${jobId} failed: ${message}`);
      send({ type: 'job_failed', code, message: message.slice(0, 400) });
    } finally {
      await fs.rm(workDir, { recursive: true, force: true }).catch(() => {});
    }
  });

  // Resolves with the close code so run() can tell "retry" from "give up".
  return new Promise((resolve) => {
    const done = (why, code) => {
      clearInterval(heartbeat);
      log(`disconnected (${why})`);
      resolve(code);
    };
    ws.addEventListener('close', (e) => done(`code ${e.code}`, e.code));
    ws.addEventListener('error', () => done('socket error', null));
  });
}

/** dreamina --version prints a JSON blob, so a bare `\d+\.\d+` match lands on
 *  whatever number happens to come first. The quoted "version" key wins when
 *  one is present; otherwise fall back to the plain-text shape used by codex
 *  and gpt-image-2-skill. */
export function parseVersion(text) {
  const raw = String(text ?? '');
  const json = raw.match(/"version"\s*:\s*"([^"]+)"/);
  if (json) return json[1];
  const m = raw.match(/\d+\.\d+[.\d]*/);
  if (m) return m[0];
  return raw.trim().slice(0, 20) || 'installed';
}

function versionOf(bin) {
  return new Promise((resolve) => {
    const child = spawn(bin, ['--version'], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    child.stdout?.on('data', (d) => (out += d));
    child.stderr?.on('data', (d) => (out += d));
    child.on('error', () => resolve(null));
    child.on('close', () => resolve(parseVersion(out)));
  });
}

/** Tell the user NOW what will fail later — a daemon that connects happily
 *  and then bounces every job with cli_missing is technically "typed
 *  failure", practically a puzzle. The same report is sent to nous on
 *  connect so the settings page can show it (IC's 检测 CLI panel UX). */
async function preflight() {
  const codexVersion = await versionOf('codex');
  const skillVersion = await versionOf('gpt-image-2-skill');
  const dreaminaVersion = await versionOf('dreamina');
  let dreaminaAuthOk = false;
  try {
    await fs.access(
      path.join(os.homedir(), '.local', 'share', 'dreamina', 'byted_cli_user_token.json'),
    );
    dreaminaAuthOk = true;
  } catch { /* not logged in */ }
  let authOk = true;
  try {
    await fs.access(path.join(os.homedir(), '.codex', 'auth.json'));
  } catch {
    authOk = false;
  }
  const problems = [];
  if (!skillVersion) {
    problems.push(
      'gpt-image-2-skill not found — image jobs will fail. Install: npm i -g gpt-image-2-skill',
    );
  }
  if (!codexVersion) {
    problems.push(
      'codex CLI not found — text jobs will fail. Install: npm i -g @openai/codex',
    );
  }
  if (!authOk) {
    problems.push('no codex login found (~/.codex/auth.json) — run: codex login');
  }
  for (const problem of problems) log(`WARNING: ${problem}`);
  if (!problems.length) log('preflight ok: codex login + CLIs found');
  if (!dreaminaVersion) {
    log('note: dreamina CLI not found — 即梦 jobs disabled on this device');
  } else if (!dreaminaAuthOk) {
    log('note: dreamina not logged in — run: dreamina login');
  }
  return {
    codex_ok: Boolean(codexVersion),
    codex_version: codexVersion,
    skill_ok: Boolean(skillVersion),
    skill_version: skillVersion,
    auth_ok: authOk,
    dreamina_ok: Boolean(dreaminaVersion),
    dreamina_version: dreaminaVersion,
    dreamina_auth_ok: dreaminaAuthOk,
    node_version: process.version,
    platform: process.platform,
  };
}

async function run() {
  const cfg = await readConfig();
  if (!cfg?.device_token) {
    throw new Error('not paired yet — run: nous-codex pair <CODE>');
  }
  const envReport = await preflight();
  cfg.envReport = envReport;
  let backoff = RECONNECT_MIN_MS;
  for (;;) {
    const started = Date.now();
    const closeCode = await connect(cfg);
    const stopReason = stopReasonForCloseCode(closeCode);
    if (stopReason) {
      // Terminal, not transient: this token will never start working again.
      // Exit 0 on purpose — systemd's Restart=on-failure and launchd's
      // KeepAlive/SuccessfulExit=false both leave a clean exit alone, so one
      // exit code gives the same "stop for good" semantics on all three
      // platforms, with no RestartPreventExitStatus and no self-bootout. The
      // reason is not lost: it goes to last_stop.json, which `status` reads.
      await writeLastStop(stopReason);
      console.error(STOP_REASON_TEXT[stopReason]);
      process.exit(0);
    }
    // A connection that lived a while means the endpoint is healthy —
    // reset the backoff so a nightly blip doesn't leave us at 30s forever.
    if (Date.now() - started > 60_000) backoff = RECONNECT_MIN_MS;
    log(`reconnecting in ${Math.round(backoff / 1000)}s`);
    await new Promise((r) => setTimeout(r, backoff));
    backoff = Math.min(backoff * 2, RECONNECT_MAX_MS);
  }
}

// ── service installation ──────────────────────────────────────────────────

function scriptPath() {
  return fileURLToPath(import.meta.url);
}

/** systemd unit files split unquoted values on whitespace, so a PATH or an
 *  install directory containing a space is silently truncated — `/home/my
 *  tools/bin` becomes `/home/my`, and the only symptom is every CLI reporting
 *  as missing. Quote the value, escape backslash and quote, and double `%`
 *  (systemd expands `%x` specifiers even inside quotes). */
export function systemdQuote(value) {
  return `"${String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/%/g, '%%')}"`;
}

/** plist values are XML text: an install path with `&` or `<` in it would
 *  otherwise produce a plist launchd refuses to parse. */
export function escapeXml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

/** systemd --user unit. Pure so its content is asserted by tests rather than
 *  "it looked right the one time I ran it".
 *
 *  `pathEnv` matters more than it looks: `systemd --user` starts services with
 *  a minimal PATH (no ~/.local/bin, no linuxbrew), so a daemon installed from
 *  a shell where all three CLIs resolve would come up reporting every one of
 *  them missing and bounce every job with cli_missing. Baking the installing
 *  shell's PATH in is what makes the service equivalent to `run`. */
export function renderSystemdUnit({ nodePath, scriptPath: script, apiBase = null, pathEnv = null }) {
  const env =
    (pathEnv ? `Environment=PATH=${systemdQuote(pathEnv)}\n` : '') +
    (apiBase ? `Environment=NOUS_API_BASE=${systemdQuote(apiBase)}\n` : '');
  return `[Unit]
Description=nous-codex — run nous canvas generations on this machine
Documentation=https://github.com/iocrazy/nous-app/tree/master/tools/codex-daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${systemdQuote(nodePath)} ${systemdQuote(script)} run
${env}Restart=on-failure
RestartSec=5
# on-failure is the whole revocation guard: a revoked daemon exits 0, and a
# clean exit is not a failure, so systemd leaves it stopped. Do NOT change
# this to Restart=always — that would reconnect-loop a revoked device.

[Install]
WantedBy=default.target
`;
}

/** launchd LaunchAgent plist (macOS).
 *  KeepAlive/SuccessfulExit=false means "relaunch only after a non-zero
 *  exit", which is exactly the systemd Restart=on-failure semantics: a
 *  revoked daemon exits 0 and launchd leaves it stopped. Crashes still get
 *  relaunched. Not verified on real hardware — no mac in the loop. */
export function renderLaunchdPlist({
  nodePath,
  scriptPath: script,
  logPath,
  apiBase = null,
  pathEnv = null,
}) {
  // launchd hands the job a minimal PATH too — same reasoning as the unit.
  const vars = [];
  if (pathEnv) vars.push(['PATH', pathEnv]);
  if (apiBase) vars.push(['NOUS_API_BASE', apiBase]);
  const envBlock = vars.length
    ? `  <key>EnvironmentVariables</key>
  <dict>
${vars
  .map(([k, v]) => `    <key>${escapeXml(k)}</key>\n    <string>${escapeXml(v)}</string>`)
  .join('\n')}
  </dict>
`
    : '';
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${SERVICE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${escapeXml(nodePath)}</string>
    <string>${escapeXml(script)}</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
${envBlock}  <key>StandardOutPath</key>
  <string>${escapeXml(logPath)}</string>
  <key>StandardErrorPath</key>
  <string>${escapeXml(logPath)}</string>
</dict>
</plist>
`;
}

function systemdUnitPath() {
  return path.join(xdgConfigHome(), 'systemd', 'user', SYSTEMD_UNIT_NAME);
}

function launchdPlistPath() {
  return path.join(os.homedir(), 'Library', 'LaunchAgents', `${SERVICE_LABEL}.plist`);
}

async function installServiceLinux() {
  const unitPath = systemdUnitPath();
  await fs.mkdir(path.dirname(unitPath), { recursive: true });
  await fs.writeFile(
    unitPath,
    renderSystemdUnit({
      nodePath: process.execPath,
      scriptPath: scriptPath(),
      apiBase: process.env.NOUS_API_BASE || null,
      pathEnv: process.env.PATH || null,
    }),
  );
  log(`wrote ${unitPath}`);
  await requireCommand('systemctl', ['--user', 'daemon-reload']);
  await requireCommand('systemctl', ['--user', 'enable', SYSTEMD_UNIT_NAME]);
  // `enable --now` is a no-op on an already-active unit, so re-running the
  // installer after an upgrade would leave the OLD daemon running while
  // `status` reported everything green. restart is the only verb that both
  // starts a stopped unit and reloads a running one.
  await requireCommand('systemctl', ['--user', 'restart', SYSTEMD_UNIT_NAME]);
  // Without linger the unit dies at logout and never starts at boot on a
  // headless box. Not fatal — some systems forbid it.
  const linger = await tryCommand('loginctl', ['enable-linger', os.userInfo().username]);
  if (!linger.ok) {
    log('WARNING: loginctl enable-linger failed — the service will only run while you are logged in');
  }
  log(`installed. check: systemctl --user status ${SYSTEMD_UNIT_NAME}`);
}

async function uninstallServiceLinux() {
  await tryCommand('systemctl', ['--user', 'disable', '--now', SYSTEMD_UNIT_NAME]);
  const unitPath = systemdUnitPath();
  await fs.rm(unitPath, { force: true });
  await tryCommand('systemctl', ['--user', 'daemon-reload']);
  await tryCommand('systemctl', ['--user', 'reset-failed', SYSTEMD_UNIT_NAME]);
  log(`removed ${unitPath}`);
}

async function installServiceDarwin() {
  const plistPath = launchdPlistPath();
  const logPath = path.join(os.homedir(), 'Library', 'Logs', 'nous-codex.log');
  await fs.mkdir(path.dirname(plistPath), { recursive: true });
  await fs.mkdir(path.dirname(logPath), { recursive: true });
  await fs.writeFile(
    plistPath,
    renderLaunchdPlist({
      nodePath: process.execPath,
      scriptPath: scriptPath(),
      logPath,
      apiBase: process.env.NOUS_API_BASE || null,
      pathEnv: process.env.PATH || null,
    }),
  );
  log(`wrote ${plistPath}`);
  const uid = process.getuid?.() ?? 501;
  // Both `bootstrap` and the legacy `load -w` fail on an already-loaded
  // label, which would make a second install-service (an upgrade) throw
  // instead of picking up the new code. Unload first; failing here just
  // means it was not loaded.
  await tryCommand('launchctl', ['bootout', `gui/${uid}/${SERVICE_LABEL}`]);
  const boot = await tryCommand('launchctl', ['bootstrap', `gui/${uid}`, plistPath]);
  if (!boot.ok) {
    // Older syntax, still accepted on current macOS.
    await requireCommand('launchctl', ['load', '-w', plistPath]);
  }
  log(`installed. logs: ${logPath}`);
}

async function uninstallServiceDarwin() {
  const plistPath = launchdPlistPath();
  const uid = process.getuid?.() ?? 501;
  const out = await tryCommand('launchctl', ['bootout', `gui/${uid}/${SERVICE_LABEL}`]);
  if (!out.ok) await tryCommand('launchctl', ['unload', '-w', plistPath]);
  await fs.rm(plistPath, { force: true });
  log(`removed ${plistPath}`);
}

async function installServiceWin32() {
  // ⚠️ NOT verified on real Windows hardware.
  //
  // Unlike the unit and the plist, a scheduled task carries no environment of
  // its own: schtasks has no equivalent of Environment= / EnvironmentVariables.
  // So NOUS_API_BASE must be set as a *user* environment variable (setx, or
  // System Properties → Environment Variables) for the task to see it — the
  // README says so rather than pretending this line handles it.
  if (process.env.NOUS_API_BASE) {
    log(
      'NOTE: NOUS_API_BASE is NOT copied into the scheduled task. Set it as a user '
        + 'environment variable (setx NOUS_API_BASE "…") or the service will use the default.',
    );
  }
  const tr = `"${process.execPath}" "${scriptPath()}" run`;
  await requireCommand('schtasks', [
    '/Create', '/SC', 'ONLOGON', '/TN', WIN_TASK_NAME, '/TR', tr, '/F',
  ]);
  log(`installed scheduled task ${WIN_TASK_NAME} (untested on real hardware)`);
}

async function uninstallServiceWin32() {
  // Idempotent like the other two: removing an absent task is success, not an
  // error the user has to interpret.
  const r = await tryCommand('schtasks', ['/Delete', '/TN', WIN_TASK_NAME, '/F']);
  log(r.ok ? `removed scheduled task ${WIN_TASK_NAME}` : `no scheduled task ${WIN_TASK_NAME} to remove`);
}

async function installService() {
  const cfg = await readConfig();
  if (!cfg?.device_token) {
    throw new Error('not paired yet — run: nous-codex pair <CODE> first');
  }
  if (process.platform === 'linux') return installServiceLinux();
  if (process.platform === 'darwin') return installServiceDarwin();
  if (process.platform === 'win32') return installServiceWin32();
  throw new Error(
    `install-service is not supported on ${process.platform} — keep \`nous-codex run\` open yourself`,
  );
}

async function uninstallService() {
  if (process.platform === 'linux') return uninstallServiceLinux();
  if (process.platform === 'darwin') return uninstallServiceDarwin();
  if (process.platform === 'win32') return uninstallServiceWin32();
  throw new Error(`uninstall-service is not supported on ${process.platform}`);
}

// ── status ────────────────────────────────────────────────────────────────

async function fileExists(p) {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

async function serviceStatus() {
  if (process.platform === 'linux') {
    const unitPath = systemdUnitPath();
    const installed = await fileExists(unitPath);
    const active = await tryCommand('systemctl', ['--user', 'is-active', SYSTEMD_UNIT_NAME]);
    return {
      kind: 'systemd',
      path: unitPath,
      installed,
      state: (active.out || active.err).trim() || 'unknown',
    };
  }
  if (process.platform === 'darwin') {
    const plistPath = launchdPlistPath();
    const installed = await fileExists(plistPath);
    const uid = process.getuid?.() ?? 501;
    const printed = await tryCommand('launchctl', ['print', `gui/${uid}/${SERVICE_LABEL}`]);
    return {
      kind: 'launchd',
      path: plistPath,
      installed,
      state: printed.ok ? 'loaded' : 'not loaded',
    };
  }
  if (process.platform === 'win32') {
    const q = await tryCommand('schtasks', ['/Query', '/TN', WIN_TASK_NAME]);
    return {
      kind: 'schtasks',
      path: WIN_TASK_NAME,
      installed: q.ok,
      state: q.ok ? 'registered' : 'absent',
    };
  }
  return { kind: 'none', path: null, installed: false, state: 'unsupported platform' };
}

async function status() {
  const cfg = await readConfig();
  console.log(`config file : ${CONFIG_FILE} ${cfg ? '(present)' : '(MISSING — not paired)'}`);
  console.log(`device id   : ${cfg?.device_id ?? '(none)'}`);
  console.log(`last stop   : ${describeLastStop(await readLastStop()) ?? '(none recorded)'}`);
  console.log(`api base    : ${API_BASE}`);
  console.log(`script      : ${scriptPath()}`);
  console.log(`node        : ${process.execPath} ${process.version}`);

  const svc = await serviceStatus();
  console.log(
    `service     : ${svc.kind} — ${svc.installed ? 'installed' : 'not installed'} (${svc.path ?? '-'})`,
  );
  console.log(`service run : ${svc.state}`);

  console.log('--- CLI preflight ---');
  const report = await preflight();
  for (const [k, v] of Object.entries(report)) console.log(`${k.padEnd(18)}: ${v}`);
}

// ── entry ─────────────────────────────────────────────────────────────────

const USAGE = `usage:
  nous-codex pair <CODE> [--name <device>]   pair this machine with your nous account
  nous-codex run                             stay connected and take jobs (foreground)
  nous-codex install-service                 start at login/boot and restart on crash
  nous-codex uninstall-service               remove that service
  nous-codex status                          config / service / CLI report`;

export async function main(argv = process.argv.slice(2)) {
  const [cmd, ...rest] = argv;
  if (cmd === 'pair') await pair(rest);
  else if (cmd === 'run') await run();
  else if (cmd === 'install-service') await installService();
  else if (cmd === 'uninstall-service') await uninstallService();
  else if (cmd === 'status') await status();
  else {
    console.log(USAGE);
    process.exit(1);
  }
}

async function isMainModule() {
  const entry = process.argv[1];
  if (!entry) return false;
  const self = scriptPath();
  if (path.resolve(entry) === self) return true;
  // npm installs the bin as a symlink; compare resolved targets too.
  try {
    return (await fs.realpath(entry)) === (await fs.realpath(self));
  } catch {
    return false;
  }
}

if (await isMainModule()) {
  try {
    await main();
  } catch (err) {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }
}
