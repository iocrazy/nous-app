/**
 * TipTap editing-surface switch — admin-controlled via the module registry
 * (system_settings['editor.tiptap_surface'], the project's single toggle
 * convention; see backend app/services/modules/registry.py). Opt-in, FAILS
 * CLOSED: any error → false → the proven legacy engine.
 *
 * Lives in its OWN module (not sceneService) deliberately: ~30 test files
 * partially mock '../sceneService', and vitest throws on ACCESS of a missing
 * mock export — a new export there breaks every one of them. Nobody mocks
 * this path, so EditorShell can import it safely.
 *
 * localStorage 'editor.tiptap' remains a per-browser EMERGENCY override on
 * top (see ./flag.ts) — the admin switch is the official control.
 */
import { getApiUrl } from '../../utils/apiConfig';
import { getAuthHeaders } from '../../services/parserService';
import { unwrapResponse } from '../../utils/apiHelpers';

export async function fetchTiptapModuleStatus(): Promise<boolean> {
  try {
    const headers = await getAuthHeaders();
    const res = await fetch(`${getApiUrl()}/api/v1/editor/tiptap-module-status`, {
      headers,
    });
    const data = await unwrapResponse<{ enabled?: boolean }>(res);
    return data.enabled === true;
  } catch (err) {
    // Skip the log under vitest: jsdom fetch always rejects here, so every
    // EditorShell mount emits this asynchronously — often AFTER the test
    // file's teardown, which kills the worker with "Closing rpc while
    // onUserConsoleLog was pending" (the recurring CI flake, #1260 class).
    // Prod behavior is unchanged: fail-closed with a loud log.
    if (import.meta.env.MODE !== 'test') {
      console.error('[editor] tiptap module-status load failed (fail-closed)', err);
    }
    return false;
  }
}
