/**
 * Pure logic extracted from MCPServersPanel for unit testing.
 *
 * The MCPServersPanel component itself is mostly UI; the logic that
 * actually has correctness conditions (form validation, name regex,
 * patch building) lives here so it can be tested without spinning up
 * a React renderer.
 */

export const MCP_SERVER_NAME_RE = /^[A-Za-z0-9_]+$/;

export interface MCPServerFormValues {
  name: string;
  url: string;
  bearer_token: string;
  description: string;
  enabled: boolean;
}

export const EMPTY_MCP_FORM: MCPServerFormValues = {
  name: '',
  url: '',
  bearer_token: '',
  description: '',
  enabled: true,
};

/**
 * Validate the form. Returns null on success, OR an i18n KEY on failure
 * (caller passes through t()). Keys live under "mcp.errors.*" in the
 * locale files.
 *
 * - When ``creating`` is true, name must be present and match the
 *   alphanumeric+underscore regex (server uses '.' as namespace separator,
 *   so it MUST be excluded — a name like "bad.name" would silently
 *   collide with the prefix split).
 * - URL is always required and must start with http:// or https://.
 *   Other validation (DNS resolvability, MCP handshake) happens
 *   server-side at the first chat that tries to use this server.
 */
export function validateMCPServerForm(
  form: MCPServerFormValues,
  creating: boolean,
): string | null {
  if (creating) {
    if (!form.name.trim()) return 'mcp.errors.nameRequired';
    if (!MCP_SERVER_NAME_RE.test(form.name)) return 'mcp.errors.nameInvalid';
  }
  if (!form.url.trim()) return 'mcp.errors.urlRequired';
  if (!/^https?:\/\//.test(form.url)) return 'mcp.errors.urlScheme';
  return null;
}

/**
 * Build the PATCH payload for an edit. Empty bearer_token = "keep
 * existing" — distinguishes from "explicitly clear" which we don't yet
 * support (the UI has no way to express that).
 */
export interface MCPServerUpdatePayload {
  url?: string;
  bearer_token?: string;
  description?: string;
  enabled?: boolean;
}

export function buildMCPServerUpdatePayload(
  form: MCPServerFormValues,
): MCPServerUpdatePayload {
  const patch: MCPServerUpdatePayload = {
    url: form.url.trim(),
    description: form.description.trim() || undefined,
    enabled: form.enabled,
  };
  if (form.bearer_token.trim()) {
    patch.bearer_token = form.bearer_token.trim();
  }
  return patch;
}
