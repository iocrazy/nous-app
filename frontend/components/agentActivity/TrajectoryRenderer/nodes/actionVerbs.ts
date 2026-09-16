/**
 * 一行动作该读成「做了什么」，不是「调了哪个函数」（3c §4.1）。原先只写工具名，一次
 * 回合读成 `ListShots · UpdateShot`——对着作品的人看不懂。未映射的工具**不猜动词**：
 * 原样用工具名 + 主参数，猜错的动词比没有动词更坏。
 */

const MAX_OBJ = 40;

/** 工具 → [动词, 主参数键（按序取第一个有值的；空数组 = 这个动作没有对象）]。 */
const VERBS: Record<string, [string, string[]]> = {
  ReadScene: ['Read scene', ['scene_id']],
  ListShots: ['Listed shots', []],
  UpdateShot: ['Edited shot', ['shot_id']],
  CreateShot: ['Added shot', ['shot_id']],
  GenerateShotImage: ['Generated image', ['shot_id']],
  GenerateImage: ['Generated image', ['shot_id', 'prompt']],
  RunCommand: ['Ran', ['command']],
};

/** 未映射工具的主参数候选，按优先级；都没有就只留工具名。 */
const FALLBACK_KEYS = ['skill', 'description', 'name', 'command', 'prompt', 'id'];

const trim = (s: string): string => (s.length > MAX_OBJ ? `${s.slice(0, MAX_OBJ)}…` : s);

function pick(args: unknown, keys: string[]): string | null {
  if (!args || typeof args !== 'object' || Array.isArray(args)) return null;
  const bag = args as Record<string, unknown>;
  for (const k of keys) {
    const v = bag[k];
    if (typeof v === 'string' && v.trim()) return trim(v.trim());
    if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  }
  return null;
}

export function actionLabel(tool: string, args: unknown, ok: boolean): string {
  const [verb, keys] = VERBS[tool] ?? [tool, FALLBACK_KEYS];
  const head = [verb, pick(args, keys)].filter(Boolean).join(' ');
  return ok ? head : `${head} failed`;
}
