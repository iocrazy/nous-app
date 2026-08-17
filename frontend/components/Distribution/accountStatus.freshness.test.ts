/**
 * `describeSessionFreshness` — 「上次确认这个账号还活着是什么时候」。
 *
 * 这个函数存在的理由是一个盲区：`status: 'active'` 只代表**没有观察到失败**，
 * 而巡检（`session_health_check.py`，每 30 分钟一 tick、单账号最少隔 6 小时）
 * 可能压根还没轮到这个账号。在此之前，「一小时前刚验过」和「从绑上到现在一次
 * 都没验过」在 UI 上长得一模一样 —— 没有证据被渲染成了肯定的结论。
 *
 * 所以下面钉的是这几件事，每一条都能被一个**看似合理的**错实现打红：
 *
 *  * `session_checked_at` 为 null → `never`，**绝不是** `checked`（把 null 当
 *    成 0 秒前，正是那个盲区的代码形态）；
 *  * oauth 账号 → `notApplicable`。巡检只扫 session 行，所以它们那一列结构性
 *    永远是 null；给它们标「从未校验」是凭空造一个警报；
 *  * 拿到手的值解析不出来 → `unknown`。并进 `never` 是断言一件没观察到的事，
 *    并进 `checked` 会渲染出 `NaN`；
 *  * 时钟偏斜（时间戳在未来）不产出负数年龄；
 *  * 分钟/小时/天的分档在**边界两侧**都验，避免「反正都返回 checked」这种一半
 *    正确的实现蒙混过关。
 *
 * ⚠️ 这里刻意**没有**「多久算过期」的断言 —— 因为代码里没有那个判据可依。
 * 复检间隔是后端的 `MIN_RECHECK_INTERVAL_S`，还能被环境变量改，前端抄一份数字
 * 就是把猜测显示成事实。函数只报年龄。
 */
import { describe, it, expect } from 'vitest';
import { describeSessionFreshness } from './accountStatus';
import type { SocialAccount } from '../../types';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** 固定的「现在」，避免用例自己变成时钟的函数。 */
const NOW = Date.parse('2026-08-16T12:00:00Z');

/**
 * 一行 session 账号。
 *
 * ⚠️ 形状照抄后端 `SocialAccountOut`（CLAUDE.md「边界 mock 必须用真实 JSON
 * 形状」）：`session_checked_at` 是 Pydantic datetime 序列化出来的 **ISO 字符
 * 串**，不是 Date 对象；`id` 是 str（repo 的 `_public_row` 已转）。
 */
const account = (over: Partial<SocialAccount> = {}): SocialAccount => ({
  id: '727145299382534148',
  scope_type: 'user',
  scope_id: 'u1',
  platform: 'douyin',
  platform_user_id: 'op4',
  username: 'Matrix Two',
  avatar_url: null,
  token_expires_at: null,
  auth_type: 'session',
  status: 'active',
  session_checked_at: null,
  created_at: '2026-07-04T00:00:00Z',
  ...over,
});

/** `now - ms` 的 ISO 串 —— 后端就是这么发过来的。 */
const agoIso = (ms: number) => new Date(NOW - ms).toISOString();

describe('describeSessionFreshness', () => {
  it('reports "never" for a session account that has not been checked yet', () => {
    // 本次改动的靶心：这一行绝不能读成「一切正常」。
    expect(describeSessionFreshness(account({ session_checked_at: null }), NOW))
      .toEqual({ kind: 'never' });
  });

  it('reports "never" when the field is absent altogether', () => {
    // 后端 schema 里它是 Optional 且有默认值，老响应体可能整个字段都没有。
    const { session_checked_at: _omitted, ...rest } = account();
    expect(describeSessionFreshness(rest as SocialAccount, NOW))
      .toEqual({ kind: 'never' });
  });

  it('does not confuse "never checked" with "just checked"', () => {
    // 这两条并排放，是因为把 null 当成 0 秒前的实现能让上一条以外的每条都过。
    const never = describeSessionFreshness(account({ session_checked_at: null }), NOW);
    const fresh = describeSessionFreshness(
      account({ session_checked_at: agoIso(10_000) }), NOW,
    );
    expect(never.kind).toBe('never');
    expect(fresh.kind).toBe('checked');
    expect(never).not.toEqual(fresh);
  });

  it('says session checks do not apply to an oauth account', () => {
    // 巡检只扫 session 行 —— oauth 那一列永远是 null，标「从未校验」是造警报。
    expect(describeSessionFreshness(
      account({ auth_type: 'oauth', session_checked_at: null }), NOW,
    )).toEqual({ kind: 'notApplicable' });
  });

  it('still says notApplicable for an oauth row that somehow carries a timestamp', () => {
    // 绑定方式才是判据，不是「这一列有没有值」。
    expect(describeSessionFreshness(
      account({ auth_type: 'oauth', session_checked_at: agoIso(2 * HOUR) }), NOW,
    )).toEqual({ kind: 'notApplicable' });
  });

  it('reports "unknown" for an unparseable timestamp instead of rendering NaN', () => {
    expect(describeSessionFreshness(
      account({ session_checked_at: 'not-a-date' }), NOW,
    )).toEqual({ kind: 'unknown' });
  });

  it('treats a just-checked account as "now" rather than "0 minutes ago"', () => {
    expect(describeSessionFreshness(account({ session_checked_at: agoIso(0) }), NOW))
      .toEqual({ kind: 'checked', unit: 'now', value: 0 });
    expect(describeSessionFreshness(account({ session_checked_at: agoIso(59_000) }), NOW))
      .toEqual({ kind: 'checked', unit: 'now', value: 0 });
  });

  it('does not report a negative age when the timestamp is in the future', () => {
    // DB 与浏览器之间的时钟偏斜。"3 分钟后校验" 不是可以显示出去的东西。
    expect(describeSessionFreshness(
      account({ session_checked_at: new Date(NOW + 3 * MINUTE).toISOString() }), NOW,
    )).toEqual({ kind: 'checked', unit: 'now', value: 0 });
  });

  it('buckets minutes, hours and days — checked on both sides of each boundary', () => {
    const at = (ms: number) => describeSessionFreshness(
      account({ session_checked_at: agoIso(ms) }), NOW,
    );

    expect(at(MINUTE)).toEqual({ kind: 'checked', unit: 'minutes', value: 1 });
    expect(at(45 * MINUTE)).toEqual({ kind: 'checked', unit: 'minutes', value: 45 });
    expect(at(HOUR - 1)).toEqual({ kind: 'checked', unit: 'minutes', value: 59 });

    expect(at(HOUR)).toEqual({ kind: 'checked', unit: 'hours', value: 1 });
    expect(at(3 * HOUR + 40 * MINUTE)).toEqual({ kind: 'checked', unit: 'hours', value: 3 });
    expect(at(DAY - 1)).toEqual({ kind: 'checked', unit: 'hours', value: 23 });

    expect(at(DAY)).toEqual({ kind: 'checked', unit: 'days', value: 1 });
    expect(at(9 * DAY + 5 * HOUR)).toEqual({ kind: 'checked', unit: 'days', value: 9 });
  });
});
