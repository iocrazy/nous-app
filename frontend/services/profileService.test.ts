/**
 * profileService — 拒绝的理由必须活着穿过边界。
 *
 * ⚠️ 这个文件的 fixture 用的是**生产真实的错误体形状**，不是 FastAPI 的裸
 * `{detail}`（CLAUDE.md「边界 mock 必须用真实 JSON 形状」+ 2026-09-09 那条）。
 * 生产把每个 HTTPException 包成
 * `{success, error, code: "http_<status>", request_id, details}`，而我们真正要
 * 分支的理由（`username_taken` 还是 `username_invalid`）在 `details.code` 里。
 * 外层的 `code` 永远只是 `http_409`，区分不了这两种 —— 拿裸形状写 fixture 的
 * 解析器在单测里会全绿，真栈上每个拒绝都退化成一句「操作失败」。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { fetchProfile, updateUsername, ProfileError } from './profileService';

vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer test' }),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

describe('profileService', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns the account name and the stable id', async () => {
    vi.mocked(fetch).mockResolvedValue(
      json(200, { username: 'iocrazy', display_id: '2103081632', avatar_url: null }),
    );

    const p = await fetchProfile();

    expect(p.username).toBe('iocrazy');
    // display_id 是 BIGINT snowflake：出闸必须是字符串，超过 2^53 的数字
    // 在 JS 里会掉精度（5.3 陷阱）。
    expect(p.display_id).toBe('2103081632');
    expect(typeof p.display_id).toBe('string');
  });

  it('PATCHes the name it was given', async () => {
    vi.mocked(fetch).mockResolvedValue(json(200, { username: '张三', display_id: '1' }));

    await updateUsername('张三');

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/auth/profile');
    expect(init?.method).toBe('PATCH');
    expect(JSON.parse(String(init?.body))).toEqual({ username: '张三' });
  });

  it('surfaces username_taken from details.code, not the http_409 envelope', async () => {
    // 生产的原样形状。`code` 是 http_409（两种拒绝都是它），真正的理由在 details 里。
    vi.mocked(fetch).mockResolvedValue(
      json(409, {
        success: false,
        error: 'That name is taken',
        code: 'http_409',
        request_id: 'req_abc',
        details: { code: 'username_taken', message: 'That name is taken' },
      }),
    );

    await expect(updateUsername('iocrazy')).rejects.toMatchObject({
      code: 'username_taken',
      message: 'That name is taken',
    });
  });

  it('tells username_invalid apart from username_taken', async () => {
    // 同一个 http_422 外壳下的另一种理由 —— UI 要据此给不同的提示。
    vi.mocked(fetch).mockResolvedValue(
      json(422, {
        success: false,
        error: 'Unprocessable Entity',
        code: 'http_422',
        details: { code: 'username_invalid', message: '2-30 characters; …' },
      }),
    );

    const err = await updateUsername('a').catch((e) => e);

    expect(err).toBeInstanceOf(ProfileError);
    expect(err.code).toBe('username_invalid');
    // 规则本身要传到 UI，否则用户只能猜哪里不合法。
    expect(err.message).toContain('2-30');
  });

  it('still says something useful when the body is not JSON', async () => {
    // 网关 502 之类：身体是 HTML。解析失败不该把异常吞掉变成 undefined。
    vi.mocked(fetch).mockResolvedValue(
      new Response('<html>502</html>', { status: 502 }),
    );

    const err = await updateUsername('x').catch((e) => e);

    expect(err).toBeInstanceOf(ProfileError);
    expect(err.message).toContain('502');
  });
});
