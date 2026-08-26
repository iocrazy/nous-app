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
 *   nous-codex pair <CODE>   pair this machine with your nous account
 *   nous-codex run           stay connected and take jobs
 *
 * Safety: only two commands are ever executed — `codex exec` and
 * `gpt-image-2-skill images` — with argv arrays, never a shell string. The
 * server cannot make this process run anything else.
 */

import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const API_BASE = process.env.NOUS_API_BASE || 'https://api.nous.ink';
const WS_BASE = API_BASE.replace(/^http/, 'ws');
const CONFIG_DIR = path.join(os.homedir(), '.config', 'nous-codex');
const CONFIG_FILE = path.join(CONFIG_DIR, 'config.json');
const HEARTBEAT_MS = 30_000;
const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

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

// ── pairing ───────────────────────────────────────────────────────────────

async function pair(code) {
  if (!code) throw new Error('usage: nous-codex pair <CODE>');
  const res = await fetch(`${API_BASE}/api/v1/codex-daemon/pair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code: code.trim().toUpperCase(),
      device_name: os.hostname(),
      platform: process.platform,
    }),
  });
  if (!res.ok) {
    throw new Error(`pairing failed (${res.status}): ${(await res.text()).slice(0, 200)}`);
  }
  const { data } = await res.json();
  await writeConfig({ device_id: data.device_id, device_token: data.device_token });
  log(`paired as device ${data.device_id}. run: nous-codex run`);
}

// ── job execution (WHITELIST — the only commands this process ever runs) ──

function runCommand(bin, args, { timeoutMs = 15 * 60_000 } = {}) {
  return new Promise((resolve, reject) => {
    // argv array, never a shell string: nothing the server sends can be
    // interpreted as shell syntax.
    const child = spawn(bin, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    let err = '';
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error(`${bin} timed out after ${Math.round(timeoutMs / 1000)}s`));
    }, timeoutMs);
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    child.on('error', (e) => {
      clearTimeout(timer);
      reject(new Error(`${bin} not found or not executable: ${e.message}`));
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve({ out, err });
      else reject(new Error(`${bin} exited ${code}: ${(err || out).slice(0, 400)}`));
    });
  });
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

async function runTextJob(payload) {
  const args = ['exec', '--json'];
  if (payload.model) args.push('--model', String(payload.model));
  args.push(String(payload.prompt ?? ''));
  const { out } = await runCommand('codex', args, { timeoutMs: 10 * 60_000 });
  return out;
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

async function connect(cfg) {
  const ws = new WebSocket(`${WS_BASE}/api/v1/ws/codex-agent`, {
    headers: { Authorization: `Bearer ${cfg.device_token}` },
  });
  let heartbeat;

  ws.addEventListener('open', () => {
    log('connected to nous');
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
        const text = await runTextJob(msg.payload ?? {});
        send({ type: 'job_done', text });
      } else {
        send({ type: 'job_failed', code: 'unsupported_kind', message: String(msg.kind) });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const code = /not found or not executable/.test(message)
        ? 'cli_missing'
        : /not logged in|login/i.test(message)
          ? 'codex_not_logged_in'
          : 'job_failed';
      log(`job ${jobId} failed: ${message}`);
      send({ type: 'job_failed', code, message: message.slice(0, 400) });
    } finally {
      await fs.rm(workDir, { recursive: true, force: true }).catch(() => {});
    }
  });

  return new Promise((resolve) => {
    const done = (why) => {
      clearInterval(heartbeat);
      log(`disconnected (${why})`);
      resolve();
    };
    ws.addEventListener('close', (e) => done(`code ${e.code}`));
    ws.addEventListener('error', () => done('socket error'));
  });
}

function which(bin) {
  return new Promise((resolve) => {
    const child = spawn(bin, ['--version'], { stdio: 'ignore' });
    child.on('error', () => resolve(false));
    // Some CLIs exit non-zero on --version by design; spawning at all means
    // the binary exists, which is all preflight needs to know.
    child.on('close', () => resolve(true));
  });
}

function versionOf(bin) {
  return new Promise((resolve) => {
    const child = spawn(bin, ['--version'], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    child.stdout?.on('data', (d) => (out += d));
    child.stderr?.on('data', (d) => (out += d));
    child.on('error', () => resolve(null));
    child.on('close', () => {
      const m = out.match(/\d+\.\d+[.\d]*/);
      resolve(m ? m[0] : out.trim().slice(0, 20) || 'installed');
    });
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
    await connect(cfg);
    // A connection that lived a while means the endpoint is healthy —
    // reset the backoff so a nightly blip doesn't leave us at 30s forever.
    if (Date.now() - started > 60_000) backoff = RECONNECT_MIN_MS;
    log(`reconnecting in ${Math.round(backoff / 1000)}s`);
    await new Promise((r) => setTimeout(r, backoff));
    backoff = Math.min(backoff * 2, RECONNECT_MAX_MS);
  }
}

// ── entry ─────────────────────────────────────────────────────────────────

const [cmd, arg] = process.argv.slice(2);
try {
  if (cmd === 'pair') await pair(arg);
  else if (cmd === 'run') await run();
  else {
    console.log('usage: nous-codex pair <CODE> | nous-codex run');
    process.exit(1);
  }
} catch (err) {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
}
