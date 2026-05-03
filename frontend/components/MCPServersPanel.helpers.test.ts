import { describe, it, expect } from 'vitest';
import {
  EMPTY_MCP_FORM,
  MCP_SERVER_NAME_RE,
  buildMCPServerUpdatePayload,
  validateMCPServerForm,
} from './MCPServersPanel.helpers';

describe('MCP_SERVER_NAME_RE', () => {
  it('accepts alphanumeric + underscore', () => {
    for (const n of ['notion', 'linear_v2', 'srv01', 'A_B_C']) {
      expect(MCP_SERVER_NAME_RE.test(n)).toBe(true);
    }
  });
  it('rejects dots, dashes, spaces, unicode', () => {
    for (const n of ['bad.name', 'has-dash', 'with space', 'unicodé', 'sep.tor']) {
      expect(MCP_SERVER_NAME_RE.test(n)).toBe(false);
    }
  });
});

describe('validateMCPServerForm — create mode', () => {
  it('rejects empty name', () => {
    const err = validateMCPServerForm(
      { ...EMPTY_MCP_FORM, name: '', url: 'https://x.com' },
      true,
    );
    expect(err).toMatch(/name is required/i);
  });

  it('rejects name with dots (would clash with namespace separator)', () => {
    const err = validateMCPServerForm(
      { ...EMPTY_MCP_FORM, name: 'bad.name', url: 'https://x.com' },
      true,
    );
    expect(err).toMatch(/no dots/i);
  });

  it('accepts valid create form', () => {
    expect(
      validateMCPServerForm(
        { ...EMPTY_MCP_FORM, name: 'notion', url: 'https://x.com/jsonrpc' },
        true,
      ),
    ).toBeNull();
  });
});

describe('validateMCPServerForm — edit mode (skips name check)', () => {
  it('does NOT reject empty name on edit (immutable, not in form)', () => {
    expect(
      validateMCPServerForm(
        { ...EMPTY_MCP_FORM, name: '', url: 'https://x.com' },
        false,
      ),
    ).toBeNull();
  });

  it('still rejects empty url on edit', () => {
    expect(
      validateMCPServerForm(
        { ...EMPTY_MCP_FORM, name: 'foo', url: '' },
        false,
      ),
    ).toMatch(/url is required/i);
  });

  it('rejects ftp:// url scheme (edit mode, name not required)', () => {
    expect(
      validateMCPServerForm(
        { ...EMPTY_MCP_FORM, url: 'ftp://x.com' },
        false,  // edit mode skips name check; test url validation in isolation
      ),
    ).toMatch(/http/i);
  });
});

describe('buildMCPServerUpdatePayload', () => {
  it('omits bearer_token when blank (= keep existing)', () => {
    const p = buildMCPServerUpdatePayload({
      ...EMPTY_MCP_FORM,
      url: 'https://x.com',
      bearer_token: '   ',
      description: 'd',
      enabled: true,
    });
    expect(p.bearer_token).toBeUndefined();
    expect(p.url).toBe('https://x.com');
    expect(p.enabled).toBe(true);
  });

  it('includes bearer_token when non-blank', () => {
    const p = buildMCPServerUpdatePayload({
      ...EMPTY_MCP_FORM,
      url: 'https://x.com',
      bearer_token: 'sekret',
    });
    expect(p.bearer_token).toBe('sekret');
  });

  it('trims url and description', () => {
    const p = buildMCPServerUpdatePayload({
      ...EMPTY_MCP_FORM,
      url: '  https://x.com  ',
      description: '  hello  ',
    });
    expect(p.url).toBe('https://x.com');
    expect(p.description).toBe('hello');
  });

  it('omits description when blank after trim', () => {
    const p = buildMCPServerUpdatePayload({
      ...EMPTY_MCP_FORM,
      url: 'https://x.com',
      description: '   ',
    });
    expect(p.description).toBeUndefined();
  });
});
