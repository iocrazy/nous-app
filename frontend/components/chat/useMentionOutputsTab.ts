import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { listIssueOutputs, OutputsError, type OutputObject } from '../../services/outputsService';
import {
  MIN_SEARCH_QUERY_CHARS,
  unifiedSearch,
  type SearchHit,
} from '../../services/unifiedSearchService';
import { useTurnSignal } from '../Todolist/issueTurnSignal';
import { searchHitsToMentionRows, toMentionRows, type OutputMentionRow } from './outputMentionRows';
import type { OutputMentionListHandle } from './OutputMentionList';
import type { OutputsTabProps } from './ResourcePickerSuggestion';

/**
 * The `@` picker's Outputs tab (harness 3a Task 6).
 *
 * Written to the shape of `useMentionAssetsTab` on purpose — the host routes
 * keys the same way into both, and a second, subtly different contract would
 * make "which tab claimed this keystroke" a question with two answers. Three
 * things genuinely differ:
 *
 *  1. **One host, not two.** Citations are issue-scoped (the resolver's whole
 *     check is "was this version produced on this issue"), so only the issue
 *     reply box has this tab. The chat composer refuses `output_ref` outright.
 *  2. **两套契约，按有没有查询切换。** 空查询仍是「读一次本议题的产出 + 内存
 *     过滤」——那是几行数据，往返买不到什么。有查询时范围扩到整个项目（3c
 *     §2.4），行数不再有界，于是翻成 assets 页签那一侧：每次击键重查。无
 *     project 的议题退回本议题——一个范围为空的检索是一次范围为全部的检索。
 *  3. **A failed read is SAID.** "This issue produced nothing" and "I could
 *     not find out" are answers a reader acts on differently, so the error
 *     travels as its own field rather than collapsing into an empty list.
 */

/** 跨议题检索一次要多少条产出。比本议题那条路宽一点（整个项目的产出远多于一
 *  件议题的），但仍然是一屏——`@` 的列表是用来挑的，不是用来翻的。 */
const MENTION_SEARCH_LIMIT = 12;

export interface UseMentionOutputsTabOptions {
  /** The host's own "the mention picker is showing" flag. */
  pickerOpen: boolean;
  /** The issue whose outputs are citable, or null on a host with no issue
   *  behind it. Null never fetches — the host does not draw the tab at all in
   *  that case, and a request scoped to nothing would be a request for
   *  everything. */
  issueId: number | string | null;
  /** The live `@` query, already stripped of the `@`. */
  query: string;
  /** 这件议题的编号（`MH-96`）。行只在来源与它**不同**时才标注来源 —— 检索按
   *  项目作用域，本议题自己的产出必然也在结果里并带着自己的编号，判有无会让每
   *  一行都挂上读者正看着的那件议题。null = 不比较，全标。 */
  issueKey?: string | null;
  /** 这件议题属于哪个项目。有查询时检索按它作用域（3c §2.4）。
   *
   *  `null` 不是「不限」——它是「没有可用的范围」，于是整条路退回本议题。一次
   *  没有 scope 的检索会跨掉调用方全部可见的议题，而 `@` 选一个引用时那既不是
   *  读者要的，也让他没法预期这个列表里会出现什么。 */
  projectId?: string | number | null;
  /** What the host does with a picked row (stage it as a citation). */
  onSelect: (row: OutputMentionRow) => void;
}

export interface MentionOutputsTab {
  /** Ready to spread onto `ResourcePickerSuggestion`'s `outputs` prop. */
  outputs: OutputsTabProps;
  /** Leave the tab without closing the picker (the host's `onKindChange`). */
  deactivate: () => void;
  /** ↑ / ↓ / Enter, routed from the composer into the open list. False when
   *  the tab did not claim the key, and the host must let it fall through. */
  handleKey: (key: 'ArrowUp' | 'ArrowDown' | 'Enter') => boolean;
  /** Forget the tab AND the rows. Belongs on the host's CLOSE: an issue
   *  produces outputs while its composer sits open, so a list cached from the
   *  previous mention session would hide the thing just made. */
  reset: () => void;
}

export function useMentionOutputsTab({
  pickerOpen,
  issueId,
  query,
  issueKey = null,
  projectId = null,
  onSelect,
}: UseMentionOutputsTabOptions): MentionOutputsTab {
  const { t } = useTranslation();
  const [active, setActive] = useState(false);
  const [objects, setObjects] = useState<OutputObject[] | null>(null);
  /** 检索那条路的结果。与 `objects` 并存而不是共用一个 state：两者的单位不同
   *  （一条链 vs 一版），合成一个就要在每个读它的地方再问一次「这份现在是哪
   *  种」。清查询时 `objects` 还在，列表立刻回到本议题而不是先空一下。 */
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  /**
   * What went wrong, as a CODE rather than a sentence.
   *
   * `null` — nothing went wrong. `''` — it failed with nothing typed to say
   * about it. Anything else is the server's own `details.code`.
   *
   * Storing the code rather than the rendered string is what keeps `t` out of
   * the fetch effect's dependencies, and that is not a style preference: `t`
   * gets a fresh identity on every render under some i18n setups, so an effect
   * that depends on it re-runs each render and its own cleanup cancels the
   * request that was already in flight — a tab that fetches forever and shows
   * nothing, with the guard below making sure it never retries.
   */
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const listRef = useRef<OutputMentionListHandle | null>(null);
  // Guards a second request while the first is still out: activating twice
  // (a click, then the strip re-rendering) must not double the round trip.
  const requested = useRef(false);

  /**
   * Reading once per mention session is right (§2 above) — but an issue keeps
   * producing WHILE its composer sits open, and a guard that never forgets
   * makes the thing the agent just made permanently un-citable. A finished turn
   * is the event that says the population changed.
   *
   * Declared BEFORE the fetch effect on purpose: effects run in declaration
   * order, so the guard is already down when the fetch effect re-runs on the
   * same signal. An open tab therefore re-reads rather than sitting on the
   * emptied list, which would say "this issue produced nothing" and never ask
   * again until the reader closed and reopened the mention.
   */
  const signal = useTurnSignal(issueId == null ? '' : String(issueId));
  useEffect(() => {
    if (!signal) return;
    requested.current = false;
    setObjects(null);
  }, [signal]);

  /**
   * The SAME composer can move to another issue (the detail page keeps its
   * subtree and swaps `issueId`), and the one-shot guard knows nothing about
   * that — so the tab went on offering the previous issue's outputs, which
   * are by definition un-citable here: the resolver's whole check is "was
   * this version produced on THIS issue" (B5).
   *
   * Same placement argument as the turn-signal effect above: declared before
   * the fetch effect so the guard is already down, and the stale rows already
   * gone, when that effect re-runs for the new id.
   */
  useEffect(() => {
    requested.current = false;
    setObjects(null);
    setHits(null);
    setErrorCode(null);
  }, [issueId]);

  /**
   * 有查询且有 project 时走检索。两个条件都要：没有查询就没什么可跨议题找的，
   * 没有 project 就没有可作用的范围（见 `projectId` 的注释）。
   *
   * 这个布尔同时决定读哪份 state —— 它必须由**同一个表达式**算出来，否则会出现
   * 「effect 按检索取数、`rows` 按列表渲染」这种两边各对一半的状态。
   */
  const term = query.trim();
  const searching =
    // 端点少于 MIN_SEARCH_QUERY_CHARS 个字符直接拒。输入是逐字符到达的，所以
    // 「一个字符」不是边角情况而是**每一次**搜索的第一帧 —— 放它出去等于每次
    // 用户开始打字，界面都闪一条 query_too_short。与 ⌘K 面共用同一个常量，
    // 否则「第一个字符会不会报错」在两个入口会有两个答案。
    term.length >= MIN_SEARCH_QUERY_CHARS &&
    projectId !== null &&
    projectId !== undefined &&
    String(projectId) !== '';

  /**
   * 查询短到发不出去时（1 个字符），既不搜也不把上一次搜索的错误留在屏幕上。
   *
   * 那条横幅说的是「那次搜索失败了」；挂在一份本议题产出的列表上方，它描述的
   * 是一件此刻没有发生的失败。
   */
  useEffect(() => {
    if (!searching) setErrorCode(null);
  }, [searching]);

  // 本议题那条路：读一次，内存过滤。`requested` 守卫挡住重复激活。
  useEffect(() => {
    if (!active || issueId == null || searching || requested.current) return;
    requested.current = true;
    let live = true;
    setLoading(true);
    listIssueOutputs(issueId)
      .then((rows) => {
        if (!live) return;
        setObjects(rows);
        setErrorCode(null);
      })
      .catch((err) => {
        if (!live) return;
        // Logged with the server's own words, shown with ours: the wire
        // message names a kind and a snowflake, which is a sentence for a log.
        console.error('[useMentionOutputsTab] load failed', err);
        setObjects([]);
        setErrorCode(
          err instanceof OutputsError && err.code !== `http_${err.status}`
            ? err.code
            : '',
        );
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [active, issueId, signal, searching]);

  /**
   * 跨议题那条路：每次击键重查，带 `live` 守卫与 abort。
   *
   * ⚠️ `requested` 在这里**不参与**——它记的是「本议题那份读过了」，而这条路每
   * 次查询都是一次新的问题。让它们共用一个守卫会把第二次击键变成静默 no-op。
   */
  useEffect(() => {
    if (!active || !searching) return;
    let live = true;
    const controller = new AbortController();
    setLoading(true);
    unifiedSearch(
      { q: term, kinds: ['output'], projectId, limitPerGroup: MENTION_SEARCH_LIMIT },
      controller.signal,
    )
      .then((res) => {
        if (!live) return;
        setHits(res.groups.outputs ?? []);
        setErrorCode(null);
      })
      .catch((err: unknown) => {
        if (!live) return;
        if (err instanceof DOMException && err.name === 'AbortError') return;
        console.error('[useMentionOutputsTab] search failed', err);
        setHits([]);
        const code = (err as { code?: unknown } | null)?.code;
        setErrorCode(typeof code === 'string' ? code : '');
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
      controller.abort();
      // **同步**复位，而不是靠那次请求的 `finally` —— `live` 已经是 false，那
      // 个分支再也不会跑。没有接手者的切换（关掉 mention）会把 loading 永远留
      // 在 true：下次打开是一条转着的 Loading…，而没有任何请求在飞。
      setLoading(false);
    };
  }, [active, searching, term, projectId]);

  /**
   * 失败的句子按**哪条路失败的**选，不是一句话打发两种失败。
   *
   * 两条路是互斥的（`searching` 同时决定取数和渲染），所以有查询时的失败必然来
   * 自检索。「这件议题的产出读不出来」和「这个项目搜不了」是读者会采取不同行动
   * 的两句话：前者他会重开一次 `@`，后者他会改查询词。
   */
  const error = useMemo(() => {
    if (errorCode === null) return null;
    if (searching) {
      return errorCode
        ? t('outputs.mentionSearchErrorCode', 'Could not search this project’s outputs ({{code}})', { code: errorCode })
        : t('outputs.mentionSearchError', 'Could not search this project’s outputs');
    }
    return errorCode
      ? t('outputs.errorCode', 'Could not read this output ({{code}})', { code: errorCode })
      : t('outputs.mentionError', 'Could not read this issue’s outputs');
  }, [errorCode, searching, t]);

  const rows = useMemo(() => {
    if (searching) return hits ? searchHitsToMentionRows(hits) : [];
    return objects ? toMentionRows(objects, query) : [];
  }, [searching, hits, objects, query]);

  const handleKey = useCallback(
    (key: 'ArrowUp' | 'ArrowDown' | 'Enter'): boolean => {
      if (!pickerOpen || !active) return false;
      const handle = listRef.current;
      if (!handle) return false;
      if (key === 'ArrowDown') {
        handle.move(1);
        return true;
      }
      if (key === 'ArrowUp') {
        handle.move(-1);
        return true;
      }
      // Enter. `commitActive` answers false when nothing is highlighted, and
      // that false is what lets the keystroke reach the text instead of being
      // swallowed into a pick that never happened.
      return handle.commitActive();
    },
    [pickerOpen, active],
  );

  const reset = useCallback(() => {
    setActive(false);
    setObjects(null);
    setHits(null);
    setErrorCode(null);
    requested.current = false;
  }, []);

  const deactivate = useCallback(() => setActive(false), []);

  const outputs = useMemo<OutputsTabProps>(
    () => ({
      active,
      onActivate: () => setActive(true),
      rows,
      loading,
      error,
      currentIssueKey: issueKey,
      onSelect,
      listRef,
    }),
    [active, rows, loading, error, issueKey, onSelect],
  );

  return { outputs, deactivate, handleKey, reset };
}
