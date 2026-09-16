/**
 * ⌘K 统一检索面（harness 三期 3c §2.5 / spec §6 稿一）。
 *
 * 顶栏那个放大镜从 2026 年初起就是个什么都不做的占位（`onClick` 里一句
 * `/* Cmd+K search — Phase 2+ *\/`）。这个组件是它背后的东西。
 *
 * ## 三组是一个列表
 *
 * 后端分三组返回是对的（议题的相似度和产出正文的相似度不是同一把尺子上的数），
 * 但**键盘上它必须是一个列表**：三组各自循环意味着 ↓ 走到议题组末尾会绕回议题
 * 组开头，而读者看着的是屏幕上连续的一串行——他按第四下 ↓ 以为高亮在第四行，
 * 于是 Enter 打开的是他没看着的那一行。所以这里把三组平铺成一个 `flat`，
 * `activeIndex` 在它上面走并整体 wrap，组头只是画上去的分隔。
 *
 * ## 三种「没有结果」不是一种
 *
 * 还没输入（提示怎么用）、搜了没命中（点名查询词）、搜失败（点名 `details.code`）
 * ——读者对这三件事会采取不同的行动，所以它们是三个 testid、三段文案，不共用一
 * 个「No results」。同族纪律：CLAUDE.md「空输出不是否定结论」。
 *
 * ## `deep_link` 是空串的行
 *
 * 无议题的个人 run 没有可跳转的页面，后端给**空串**。`navigate('')` 不是
 * no-op——它把整个 app 跳回根路由，也就是「按了回车，页面没了」。这种行画成灰
 * 的、Enter 跳过、点击无效。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { FileOutput, ListChecks, Play, Search as SearchIcon } from 'lucide-react';

import {
  unifiedSearch,
  type SearchHit,
  type UnifiedSearchResponse,
} from '../../services/unifiedSearchService';
import { useCommandPalette } from '../../stores/commandPaletteStore';

/** 最后一次击键到发出请求之间等多久。交互式检索的取舍：短到读者感觉不到停顿，
 *  长到一个词不会打出五次请求。3c §2.5 定的值。 */
export const SEARCH_DEBOUNCE_MS = 200;

/** 少于这么多字符不发请求。端点的 `MIN_QUERY_CHARS` 是同一个数：一个字的查询
 *  在 trgm 上退化成全表匹配，而它几乎一定是「还在打字」。在这里先拦一道不是
 *  重复校验——是不让每次输入的第一个字符都换回一次注定的 400。 */
const MIN_QUERY_CHARS = 2;

/** 每组要多少条。面板是一屏，不是一页结果。 */
const LIMIT_PER_GROUP = 10;

/**
 * 失败 → 一个可显示的码。
 *
 * 读的是 `err.code`，**不是** `err instanceof UnifiedSearchError`：这个面板也会收到别
 * 处抛出的失败（网络层的 `TypeError`、将来某个包装器），而 `instanceof` 对它们
 * 一律答 false，于是「搜不成」退化成一句不点名任何东西的话。有码就用码，没码
 * 就承认没有——`''` 与 `null` 的区别在这里是「失败了但没什么可说的」与「没失
 * 败」。
 *
 * `http_<status>` 这种非类型化的码照样显示：它告诉读者这是传输层的事而不是他
 * 的查询有问题，那正是他下一步该做什么的依据。
 */
function failureCode(err: unknown): string {
  const code = (err as { code?: unknown } | null)?.code;
  return typeof code === 'string' ? code : '';
}

type GroupId = 'issues' | 'runs' | 'outputs';

const GROUPS: Array<{ id: GroupId; i18nKey: string; fallback: string }> = [
  { id: 'issues', i18nKey: 'search.groupIssues', fallback: 'Issues' },
  { id: 'runs', i18nKey: 'search.groupRuns', fallback: 'Runs' },
  { id: 'outputs', i18nKey: 'search.groupOutputs', fallback: 'Outputs' },
];

const ICONS: Record<GroupId, React.ComponentType<{ size?: number; className?: string }>> = {
  issues: ListChecks,
  runs: Play,
  outputs: FileOutput,
};

/** 一行 —— 命中加上它属于哪一组，因为平铺之后行自己不再知道。 */
interface FlatRow {
  hit: SearchHit;
  group: GroupId;
  /** 这一行是本组的第一行，列表在它上面画组头。 */
  startsGroup: boolean;
}

function flatten(result: UnifiedSearchResponse | null): FlatRow[] {
  if (!result) return [];
  const out: FlatRow[] = [];
  for (const g of GROUPS) {
    const hits = result.groups[g.id] ?? [];
    hits.forEach((hit, idx) => out.push({ hit, group: g.id, startsGroup: idx === 0 }));
  }
  return out;
}

export const CommandPalette: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const open = useCommandPalette((s) => s.open);
  const setOpen = useCommandPalette((s) => s.setOpen);

  const [query, setQuery] = useState('');
  const [result, setResult] = useState<UnifiedSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  /**
   * 失败的**码**，不是渲染好的句子。
   *
   * 与 `useMentionOutputsTab` 同一个理由：`t` 在某些 i18n 配置下每次渲染都换身
   * 份，一个依赖它的 effect 会每渲染重跑一次，而它自己的 cleanup 取消掉刚发出
   * 的请求——一个永远在搜、永远不显示的面板。
   */
  const [errorCode, setErrorCode] = useState<string | null>(null);
  /** 产生当前 `result` / `errorCode` 的那次查询。空态文案要点名它，而不是点名
   *  输入框此刻的内容——响应回来时读者可能已经又按了一个键。 */
  const [answeredFor, setAnsweredFor] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);

  const inputRef = useRef<HTMLInputElement | null>(null);

  const flat = useMemo(() => flatten(result), [result]);

  // 打开时从头开始。上一次的查询词和结果留着，等于读者一开面板就看见一份可能
  // 早就过时的答案——而它长得和刚搜出来的一模一样。
  useEffect(() => {
    if (!open) return;
    setQuery('');
    setResult(null);
    setErrorCode(null);
    setAnsweredFor('');
    setActiveIndex(0);
    // 焦点要等这一帧画完：`open` 刚翻成 true 时输入框还没进 DOM。
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  // 防抖 + 竞态守卫。`live` 与 `AbortController` 两道：前者挡住迟到的响应写进
  // state（一次被取消的 fetch 仍可能已经解析完），后者让真的作废的请求别占着
  // 连接。
  useEffect(() => {
    if (!open) return;
    const term = query.trim();
    if (term.length < MIN_QUERY_CHARS) {
      setResult(null);
      setErrorCode(null);
      setAnsweredFor('');
      setLoading(false);
      return;
    }
    let live = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      unifiedSearch({ q: term, limitPerGroup: LIMIT_PER_GROUP }, controller.signal)
        .then((res) => {
          if (!live) return;
          setResult(res);
          setErrorCode(null);
          setAnsweredFor(term);
          setActiveIndex(0);
        })
        .catch((err: unknown) => {
          if (!live) return;
          if (err instanceof DOMException && err.name === 'AbortError') return;
          // 用服务端自己的话记日志，用我们的话显示：wire message 里是一个码和一
          // 个 snowflake，那是给日志的句子。
          console.error('[CommandPalette] search failed', err);
          setResult(null);
          setErrorCode(failureCode(err));
          setAnsweredFor(term);
          setActiveIndex(0);
        })
        .finally(() => {
          if (live) setLoading(false);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      live = false;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [open, query]);

  const openHit = useCallback(
    (hit: SearchHit) => {
      // 空串 = 没有可跳转的页面。见模块 docstring。
      if (!hit.deep_link) return;
      setOpen(false);
      navigate(hit.deep_link);
    },
    [navigate, setOpen],
  );

  /**
   * 全局键。形状照 `IssueListView` 那段守卫抄：`defaultPrevented`（别抢别人已经
   * 处理过的键）、`isComposing`（输入法组合中的键不是命令）、排掉 `altKey` /
   * `shiftKey`。
   *
   * 两处刻意不同：
   *  1. **不排 `metaKey` / `ctrlKey`** —— ⌘K 本身就是组合键。
   *  2. **不排输入上下文。** 那道守卫存在的理由是 `IssueListView` 的快捷键是
   *     裸字母（`c` / `/`），会和打字撞车；一个修饰键和弦不可能被误当成打字，
   *     而 ⌘K 要在任何地方都能开——包括读者正在写评论的时候，那恰恰是最想跳去
   *     别处查一眼的时刻。
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing || e.altKey || e.shiftKey) return;
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setOpen(true);
        return;
      }
      if (!open) return;
      if (e.metaKey || e.ctrlKey) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        setOpen(false);
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (flat.length === 0) return;
        e.preventDefault();
        const delta = e.key === 'ArrowDown' ? 1 : -1;
        // 一个列表，一个环——三组是画上去的分隔，不是三个独立的游标。
        setActiveIndex((cur) => (cur + delta + flat.length) % flat.length);
        return;
      }
      if (e.key === 'Enter') {
        const row = flat[activeIndex];
        if (!row) return;
        e.preventDefault();
        openHit(row.hit);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, flat, activeIndex, openHit, setOpen]);

  if (!open) return null;

  const term = query.trim();
  const showHint = term.length < MIN_QUERY_CHARS;
  const showError = !showHint && errorCode !== null;
  const showEmpty = !showHint && !showError && result !== null && flat.length === 0;

  return (
    <div
      data-testid="command-palette"
      className="fixed inset-0 z-[120] flex items-start justify-center bg-ink-950/70 px-4 pt-[12vh]"
      onMouseDown={(e) => {
        // 只有点在遮罩本身上才关 —— 从面板里开始的拖选不该把面板关掉。
        if (e.target === e.currentTarget) setOpen(false);
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t('topbar.search', 'Search (⌘K)')}
        className="w-full max-w-2xl overflow-hidden rounded-lg border border-ink-700 bg-ink-900 shadow-2xl"
      >
        <div className="flex items-center gap-2 border-b border-ink-800 px-3 py-2.5">
          <SearchIcon size={15} className="shrink-0 text-ink-500" />
          <input
            ref={inputRef}
            data-testid="command-palette-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('search.palettePlaceholder', 'Search issues, runs and outputs')}
            maxLength={200}
            className="min-w-0 flex-1 bg-transparent text-[13px] text-ink-100 placeholder:text-ink-600 focus:outline-none"
          />
          {loading && (
            <span data-testid="command-palette-loading" className="shrink-0 text-[11px] text-ink-500">
              {t('search.paletteSearching', 'Searching…')}
            </span>
          )}
        </div>

        <div className="max-h-[52vh] overflow-y-auto py-1">
          {showHint && (
            <p data-testid="command-palette-hint" className="px-3 py-8 text-center text-[12px] text-ink-500">
              {t('search.paletteHint', 'Type at least 2 characters to search issues, runs and outputs')}
            </p>
          )}
          {showError && (
            <p data-testid="command-palette-error" className="px-3 py-8 text-center text-[12px] text-danger">
              {errorCode
                ? t('search.paletteError', 'The search could not run ({{code}})', { code: errorCode })
                : t('search.paletteErrorPlain', 'The search could not run')}
            </p>
          )}
          {showEmpty && (
            <p data-testid="command-palette-empty" className="px-3 py-8 text-center text-[12px] text-ink-500">
              {t('search.paletteEmpty', 'Nothing matches “{{q}}”', { q: answeredFor })}
            </p>
          )}

          {flat.map((row, idx) => {
            const group = GROUPS.find((g) => g.id === row.group);
            const Icon = ICONS[row.group];
            const linkless = !row.hit.deep_link;
            const active = idx === activeIndex;
            return (
              <React.Fragment key={`${row.group}:${row.hit.id}`}>
                {row.startsGroup && group && (
                  <div
                    data-testid="command-palette-group"
                    data-group={row.group}
                    className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wide text-ink-600"
                  >
                    {t(group.i18nKey, group.fallback)}
                    {/* 只有议题组的总数是精确的（投影表上没有便宜的 count），所以
                        只有它敢说「N of M」。 */}
                    {row.group === 'issues' && result && result.totals.issues > flat.filter((r) => r.group === 'issues').length && (
                      <span className="ml-1.5 normal-case tracking-normal text-ink-600">
                        {t('search.groupOf', '{{shown}} of {{total}}', {
                          shown: flat.filter((r) => r.group === 'issues').length,
                          total: result.totals.issues,
                        })}
                      </span>
                    )}
                  </div>
                )}
                <button
                  type="button"
                  data-testid="command-palette-row"
                  data-kind={row.hit.kind}
                  data-active={active ? 'true' : 'false'}
                  data-linkless={linkless ? 'true' : 'false'}
                  onMouseEnter={() => setActiveIndex(idx)}
                  onClick={() => openHit(row.hit)}
                  className={`flex w-full items-center gap-2 px-3 py-1.5 text-left ${
                    active ? 'bg-[var(--accent-soft)]' : 'hover:bg-ink-800/50'
                  } ${linkless ? 'cursor-default opacity-60' : ''}`}
                >
                  <Icon size={13} className="shrink-0 text-info" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12px] text-ink-100">{row.hit.title}</span>
                    {row.hit.snippet && (
                      <span className="block truncate text-[11px] text-ink-500">{row.hit.snippet}</span>
                    )}
                  </span>
                  {row.hit.issue_key && (
                    <span className="shrink-0 rounded border border-ink-700 px-1 text-[10px] text-ink-400">
                      {row.hit.issue_key}
                    </span>
                  )}
                  {linkless && (
                    <span className="shrink-0 text-[10px] text-ink-600">
                      {t('search.paletteNoPage', 'no page')}
                    </span>
                  )}
                </button>
              </React.Fragment>
            );
          })}
        </div>

        <div className="border-t border-ink-800 px-3 py-1.5 text-[10px] text-ink-600">
          {t('search.paletteFooter', '↑↓ navigate · ↵ open · esc close')}
        </div>
      </div>
    </div>
  );
};
