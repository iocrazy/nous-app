/**
 * 统一检索 client —— `GET /api/v1/search`（harness 三期 3c 契约 §1，Task 14）。
 *
 * **为什么不在 `searchService.ts` 里。** 那个模块已经有一个 `SearchResponse`
 * ——资源库的语义/混合检索（`/search/semantic` 等五条）的响应，字段完全不同
 * （`results` / `videos` / `total` / `search_type`），还有 `SearchField` /
 * `SearchResult` 一整套被十来个组件 import 的名字。契约把本组模型的名字定成
 * `SearchResponse`，两者同名同模块不可能并存。分成两个模块是唯一不伤既有契约的
 * 做法，与后端把这组模型放进 `schemas/unified_search.py`（而不是 `search.py`）
 * 是同一个决定的两侧。
 *
 * 同理**不提供 `SearchResponse` 别名**：一个能同时从两个模块 import 到的同名
 * 类，正是「这是哪一个 SearchResponse」这个问题的来源，而前缀存在就是为了消掉
 * 它。
 *
 * 三组命中而不是一条混排的流：议题的相似度和产出正文的相似度不是同一把尺子上
 * 的数，混排等于用一个假的全序把读者最想要的那一组压到下面。分组的决定在后端，
 * 这里只是照着搬——本模块不重排、不合并、不补字段。
 *
 * **每个 id 都是 string。** 议题 / run 是 Snowflake BIGINT（落进 JSON number
 * 会在浏览器里丢精度，本仓 `bigIntSafeFetch` 存在的同一个理由），产出是
 * `kind:ref_id:version` 这个合成键。两者一律**不解析**：产出的三个坐标在
 * `meta` 里各占一个键，切 `id` 等于把后端的拼法复制成第二份。
 *
 * **拒绝从 `details.code` 读。** 生产把每个 `HTTPException` 包进 ErrorResponse
 * 外壳，类型化码在 `details` 下而不是 `detail`——只读后者会让
 * `query_too_short` 在真栈上一律退化成 `http_400` + "400 Bad Request"，而单测
 * 因为用了 FastAPI 裸形状全绿（CLAUDE.md 2026-09-09）。
 */

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';
import { decodeErrorEnvelope } from './errorEnvelope';

/**
 * 少于这么多字符，端点直接 `query_too_short` 拒绝。
 *
 * 镜像 `backend/app/api/search_router.py::MIN_QUERY_CHARS`：一个字的查询在
 * trgm 上退化成全表匹配，而它几乎一定是「还在打字」。
 *
 * 每个调用方都必须在发请求前拦一道 —— **不是**重复校验，是不让读者输入的第一
 * 个字符换回一次注定的 400 并在界面上闪一条错误。两个调用方（⌘K 面、`@` 页签
 * 的跨议题检索）用同一个常量，否则「第一个字符会不会报错」在两处会有两个答案。
 */
export const MIN_SEARCH_QUERY_CHARS = 2;

/** 后端认得的三类。多送一个不认得的 kind 不会让整次检索失败（端点丢掉它），
 *  全都不认得才是 `unknown_kinds` 拒绝。 */
export type SearchKind = 'issue' | 'run' | 'output';

/**
 * 一条命中。
 *
 * `deep_link` 是**空串**而不是 null 时表示「这条命中没有可跳转的页面」（无议题
 * 的个人 run），渲染成不可点的行——空串和 null 在这里是同一个意思，但后端只发
 * 空串，所以类型不是可选的。
 *
 * `meta` 是按 `kind` 分的自由字典，逐 kind 的键见
 * `backend/app/schemas/unified_search.py`：
 *   - `issue` → `{status, assignee_user_id, assignee_agent_id}`
 *     ⚠️ **不是** `assignee_name`：全仓没有任何地方产出过指派人的显示名，造一个
 *     永远为 null 的字段会让读者以为「这条没指派」，而真相是「这一层拿不到名
 *     字」。契约 §1 写的是名字，实现刻意偏离——照契约写 fixture 就是在给一个
 *     后端不发的响应写测试。
 *   - `run` → `{status, model, error_code}`（**没有** `cost_cents` / `agent_name`）
 *   - `output` → `{kind, ref_id, version}`（**没有** `cited_count`——被引数属于
 *     Task 16 的引用镜像，只在血缘端点上）
 *
 * 类型写成 `Record<string, unknown>` 而不是三个 kind 各一个具体形状：读它的每
 * 一处都必须自己做运行时判型（`searchHitsToMentionRows` 的三坐标守卫就是），
 * 而一个 TS 里看着确定的字段会让人省掉那一步。
 */
export interface SearchHit {
  kind: SearchKind;
  /** 不透明身份键，只做 React key 与去重，**不解析**。 */
  id: string;
  title: string;
  snippet: string | null;
  /** 空串 = 没有可跳转的页面。 */
  deep_link: string;
  issue_key: string | null;
  issue_id: string | null;
  meta: Record<string, unknown>;
}

/** 三组命中。三个键恒在——「一次什么都没搜到」和「这一组没请求」在形状上一样，
 *  区分它们的是调用方传了哪些 `kinds`，不是响应里少一个键。 */
export interface SearchGroups {
  issues: SearchHit[];
  runs: SearchHit[];
  outputs: SearchHit[];
}

/**
 * 每组服务端一共有多少条匹配。
 *
 * ⚠️ 只有 `issues` 是精确总数（repository 自己数的）。`runs` / `outputs` 是
 * **裁剪后这一页**的条数——投影表上没有便宜的 count，而一个会骗人的总数比没有
 * 更糟。所以 UI 只对议题组显示「N of M」。
 */
export interface SearchTotals {
  issues: number;
  runs: number;
  outputs: number;
}

export interface UnifiedSearchResponse {
  groups: SearchGroups;
  totals: SearchTotals;
  /** 服务端自己量的墙钟，含两道可见性门。检索是交互式的，慢下来时读者要能分清
   *  是网络还是这个端点。 */
  took_ms: number;
}

export interface UnifiedSearchParams {
  /** 端点上限 200 字符，少于 2 个字符拿 `query_too_short`。 */
  q: string;
  /** 省略 = 三组全要。 */
  kinds?: SearchKind[];
  teamId?: string | number | null;
  projectId?: string | number | null;
  issueId?: string | number | null;
  /** 1..50，默认 10。 */
  limitPerGroup?: number;
}

/** 一次可以 BRANCH 的拒绝。与 `OutputsError` / `ScheduleRejectedError` 同形。 */
export class UnifiedSearchError extends Error {
  readonly code: string;
  readonly status: number;
  /**
   * 服务端 `details` 的**整份**载荷，不只是它的 `code`：一次拒绝常常带着文案要
   * 点名的那个事实，而这里不该替调用方挑哪些重要。
   */
  readonly details: Record<string, unknown> | null;

  constructor(
    code: string,
    status: number,
    message: string,
    details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = 'UnifiedSearchError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function reject(res: Response): Promise<never> {
  let code = `http_${res.status}`;
  let message = `${res.status} ${res.statusText}`;
  let details: Record<string, unknown> | null = null;
  try {
    const decoded = decodeErrorEnvelope(await res.json());
    details = decoded.details;
    if (decoded.code) code = decoded.code;
    if (decoded.message) message = decoded.message;
  } catch (err) {
    // 根本不是 JSON（网关的 HTML）—— 保住状态行，别把它读成一次类型化拒绝。
    console.error('[unifiedSearchService] error body was not JSON', err);
  }
  throw new UnifiedSearchError(code, res.status, message, details);
}

/** 可选 scope：`null` / `undefined` / 空串都是「没给」，不进 query string。
 *  一个 `project_id=` 的空值在后端是一次解析失败，不是一次「全部项目」。 */
function putScope(params: URLSearchParams, key: string, value: string | number | null | undefined): void {
  if (value === null || value === undefined) return;
  const text = String(value).trim();
  if (!text) return;
  params.set(key, text);
}

/**
 * 一次统一检索。
 *
 * `signal` 留给调用方做取消：面板每次击键都会重查，而一次迟到的响应盖掉新查询
 * 的结果就是用户看到「打完字后答案变回上一个」。没有 signal 时调用方自己用
 * `live` 守卫也行——两者至少要有一个。
 */
export async function unifiedSearch(
  params: UnifiedSearchParams,
  signal?: AbortSignal,
): Promise<UnifiedSearchResponse> {
  const query = new URLSearchParams({ q: params.q });
  if (params.kinds && params.kinds.length > 0) query.set('kinds', params.kinds.join(','));
  putScope(query, 'team_id', params.teamId);
  putScope(query, 'project_id', params.projectId);
  putScope(query, 'issue_id', params.issueId);
  if (params.limitPerGroup !== undefined) query.set('limit_per_group', String(params.limitPerGroup));

  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/search?${query.toString()}`, { headers, signal });
  if (!res.ok) return reject(res);
  return (await res.json()) as UnifiedSearchResponse;
}
