/**
 * Objects (what the endpoint returns) → citable versions (what the picker
 * offers). Harness 3a Task 6.
 *
 * `GET /issues/{id}/outputs` groups by OBJECT because "the agent made three
 * things" and "the agent revised one thing twice" are different facts. A
 * citation, though, names one VERSION — that is the whole point of the chip
 * carrying `version`. This is the one place the two units meet.
 *
 * Shape of the answer: every object's LATEST version first, in the endpoint's
 * own order, then everything older folded into one group below. The reader who
 * types `@` almost always means the thing as it stands now; the older versions
 * have to stay reachable (citing v1 next to v3 is how you say "this changed")
 * but must not pad the list three-deep per object before the second object
 * appears.
 *
 * Pure and React-free so the ordering and the filter are testable without a
 * popover.
 */

import type { OutputObject } from '../../services/outputsService';
import type { SearchHit } from '../../services/unifiedSearchService';
import type { DeliverableKind } from './deliverableKinds';

/** One citable version — the unit the picker offers and the chip carries. */
export interface OutputMentionRow {
  /** `kind:ref_id:version`. Version is part of the identity: one object cited
   *  at two versions is two citations, not one pick made twice. */
  key: string;
  ref_kind: string;
  ref_id: string;
  version: number;
  /** The registry's title for this version, or the object's, or null. */
  title: string | null;
  /** The issue this version was produced on, when the row came from a search
   *  that crossed issues (3c §2.4). `null` on every row read from THIS issue's
   *  own outputs — a chip naming the issue you are already looking at says
   *  nothing, and drawing it on every row would make the one row that came
   *  from somewhere else stop standing out. */
  issue_key: string | null;
  /** This is the object's newest registered version. */
  latest: boolean;
  /** First row of the Older group — the list draws the header before it.
   *  True on exactly one row in the whole list, never per object. */
  startsOlderGroup: boolean;
}

/** The kind words a query can match, so an untitled row stays findable.
 *
 *  Keyed by `DeliverableKind` for completeness: a fifth kind added in
 *  `deliverableKinds.ts` fails to compile here until it gets search words,
 *  rather than shipping a row findable only by its raw id. Exported for
 *  `deliverableKinds.test.ts`, which re-checks the coverage at runtime so the
 *  type cannot be widened back to `Record<string, …>` unnoticed. */
export const KIND_WORDS: Record<DeliverableKind, string> = {
  generated_media: 'image media generated',
  script_shot: 'shot',
  script_scene: 'scene',
  script_chapter: 'chapter',
};

/** Everything about one object a query is allowed to match. */
function haystack(obj: OutputObject): string {
  // `obj.kind` is whatever the endpoint sent — the cast asserts nothing, it
  // just lets the lookup happen; `?? ''` still answers for a kind this build
  // has never heard of. An unknown kind stays findable by title and id.
  const words = KIND_WORDS[obj.kind as DeliverableKind] ?? '';
  return [obj.title ?? '', obj.ref_id, obj.kind, words].join(' ').toLowerCase();
}

/**
 * Build the flat row list for `query`.
 *
 * The filter is per OBJECT, not per version: a title match keeps the whole
 * chain, because the reader is looking for the thing and then choosing which
 * version of it they meant.
 */
export function toMentionRows(objects: OutputObject[], query: string): OutputMentionRow[] {
  const needle = query.trim().toLowerCase();
  const matched = needle
    ? objects.filter((o) => haystack(o).includes(needle))
    : objects;

  const latest: OutputMentionRow[] = [];
  const older: OutputMentionRow[] = [];

  for (const obj of matched) {
    // Newest first, as the endpoint orders them — but sorted again rather than
    // trusted: the group split below is what decides which row is "the thing
    // as it stands now", and reading that off an assumed order would put a
    // stale version at the top of the list on any ordering change.
    const versions = [...(obj.versions ?? [])].sort((a, b) => b.version - a.version);
    versions.forEach((v, idx) => {
      const row: OutputMentionRow = {
        key: `${obj.kind}:${obj.ref_id}:${v.version}`,
        ref_kind: obj.kind,
        ref_id: obj.ref_id,
        version: v.version,
        // The version's own title when it has one — a revision may have been
        // renamed, and the citation records what was registered for THAT
        // version. Falls back to the object's title, then to null; `''` is
        // treated as absent so the row renders its kind word instead of a
        // blank line.
        title: v.title || obj.title || null,
        // This path reads ONE issue's outputs, so every row came from the issue
        // the composer is on. See the field's own note.
        issue_key: null,
        latest: idx === 0,
        startsOlderGroup: false,
      };
      (idx === 0 ? latest : older).push(row);
    });
  }

  if (older.length > 0) older[0] = { ...older[0], startsOlderGroup: true };
  return [...latest, ...older];
}

/**
 * 一条检索命中 → 一行可引用的版本（3c §2.4）。
 *
 * 与 `toMentionRows` 的区别不是「另一个数据源」，是**另一个单位**：那一条路读
 * 的是对象（一条链），这一条路读的是命中（一版）。所以 `latest` 恒 false ——
 * 标成「最新」等于对一个我们没读过链的对象下结论，而读者会据此以为「这就是它
 * 现在的样子」。同理没有 Older 分组：一版构不成「更早的那些」。
 */
export function searchHitsToMentionRows(hits: SearchHit[]): OutputMentionRow[] {
  const out: OutputMentionRow[] = [];
  for (const h of hits) {
    const kind = h.meta?.kind;
    const refId = h.meta?.ref_id;
    const version = h.meta?.version;
    // 引用是三坐标的。缺一个就拼不出 chip，塞进去只会在发帖时 400 —— 一次在
    // 用户按下发送之后才出现的失败，比一行从没出现过的行糟得多。
    // `ref_id` 只认 string：它是 Snowflake，number 形态已经丢过精度了，认下来
    // 等于把一个错的 id 拼进 key。
    if (typeof kind !== 'string' || typeof refId !== 'string' || typeof version !== 'number') {
      continue;
    }
    out.push({
      // key 由**这三个坐标**拼，不读 `h.id`。两者今天字面相同（后端的身份键是
      // 同一个拼法），但这一行的键要和 `toMentionRows` 产的行可比 —— 那一条路
      // 没有 `h.id` 可读，两种拼法会让同一版在两条路上得到两个 key。
      key: `${kind}:${refId}:${version}`,
      ref_kind: kind,
      ref_id: refId,
      version,
      // `''` 当作没有标题，与 `toMentionRows` 同一口径：行渲染 kind + id 而不是
      // 一行空白。
      title: h.title || null,
      issue_key: h.issue_key ?? null,
      latest: false,
      startsOlderGroup: false,
    });
  }
  return out;
}
