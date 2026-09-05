// services/codexDaemonService.ts
//
// Per-user codex daemon (C 方案) — pairing codes and paired-device
// management. Design: docs/superpowers/specs/2026-08-23-codex-per-user-daemon-design.md

import { apiFetch } from './apiClient';

export interface CodexDevice {
  id: string;
  device_name: string;
  platform: string;
  created_at: string | null;
  last_seen_at: string | null;
}

export const codexDaemonService = {
  /** Mint a one-shot pairing code (valid 10 minutes). */
  async createPairCode(): Promise<{ code: string; expires_in_seconds: number }> {
    const res = await apiFetch('/api/v1/codex-daemon/pair-code', { method: 'POST' });
    const body = (await res.json()) as {
      data?: { code: string; expires_in_seconds: number };
    };
    if (!body.data?.code) throw new Error('pair code request returned no code');
    return body.data;
  },

  async listDevices(): Promise<CodexDevice[]> {
    const res = await apiFetch('/api/v1/codex-daemon/devices');
    const body = (await res.json()) as { data?: CodexDevice[] };
    return body.data ?? [];
  },

  async revokeDevice(deviceId: string): Promise<void> {
    await apiFetch(`/api/v1/codex-daemon/devices/${deviceId}`, { method: 'DELETE' });
  },

  /** The orchestrator model gpt-image-2-skill runs with (``--model``).
   *  ``codex_model: null`` means "use the catalog row's default". */
  async getPreferences(): Promise<CodexPreferences> {
    const res = await apiFetch('/api/v1/codex-daemon/preferences');
    const body = (await res.json()) as { data?: CodexPreferences };
    return body.data ?? { codex_model: null, options: [] };
  },

  async savePreferences(patch: { codex_model: string | null }): Promise<CodexPreferences> {
    const res = await apiFetch('/api/v1/codex-daemon/preferences', {
      method: 'PUT',
      json: patch,
    });
    if (!res.ok) throw new Error(`preferences save failed: HTTP ${res.status}`);
    const body = (await res.json()) as { data?: CodexPreferences };
    return body.data ?? { codex_model: patch.codex_model, options: [] };
  },
};

export interface CodexPreferences {
  codex_model: string | null;
  /** Known-good orchestrator names the server offers; a custom one is allowed too. */
  options: string[];
}

/** A device counts as online when its heartbeat is younger than 90s
 *  (the server's own timeout — keep the two in step). */
export function isDeviceOnline(device: CodexDevice, now = Date.now()): boolean {
  if (!device.last_seen_at) return false;
  const seen = Date.parse(device.last_seen_at);
  return Number.isFinite(seen) && now - seen < 90_000;
}
