# Island Redesign v2 — P1a: Theme Tokens + Full Migration + Dual Theme

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all ~5,911 hardcoded `*-zinc-*` Tailwind classes with a theme-aware `ink` ladder + land the island semantic tokens and a Light/Dark/System theme switcher — with zero visual change in dark mode and zero functional change (spec D11/D12).

**Architecture:** Tailwind v4 CSS-first. We register an `ink` neutral ladder in `@theme inline` whose values come from CSS vars set per `[data-theme]`; dark values are byte-identical to zinc, so the codemod rename `*-zinc-N` → `*-ink-N` is a provable visual no-op (opacity modifiers and `hover:`/`md:` variants survive untouched). Island semantic tokens (`island`, `line`, `content` …) land alongside for Plan B (shell) and all NEW code. Light theme = a second var block (inverted ladder), exposed via `<html data-theme>` + ThemeContext + Settings 三态.

**Tech Stack:** Tailwind v4 (`@theme inline`), React 19 + TS, Node ≥20 codemod script, vitest.

**Hard constraints (spec):** D12 zero feature change · D4 no emoji · tsc error-line baseline 140 不退 · vitest 929 全过 · 每批 commit。

**Out of scope (Plan B `island-v2-p1b-shell`):** 岛框架 AppLayout/TopBar 重构、`VITE_FEATURE_ISLAND_UI` flag、彗尾返回键。Out of scope: `*-gray-*`（hex 与 zinc 不同，机械替换会变色）。

---

### Task 1: ink 阶梯 + 岛语义 token（index.css）

**Files:**
- Modify: `frontend/index.css`（现有 `@theme` 块在 L4-19；`@layer components` 的 `.btn-tint-*` 已存在，勿动）

- [ ] **Step 1: 在现有 `@theme { ... }` 块之后插入**

```css
/* ── Theme-aware neutral ladder (island redesign D11) ──────────────
   `ink` mirrors zinc 1:1 in dark mode (provable visual no-op for the
   zinc→ink codemod). Light mode re-maps the ladder. Components must
   use ink-* (or the semantic tokens below) — new `*-zinc-*` usages
   are forbidden after the migration. */
:root, [data-theme="dark"] {
  --ink-50:  #fafafa;
  --ink-100: #f4f4f5;
  --ink-200: #e4e4e7;
  --ink-300: #d4d4d8;
  --ink-400: #a1a1aa;
  --ink-500: #71717a;
  --ink-600: #52525b;
  --ink-700: #3f3f46;
  --ink-800: #27272a;
  --ink-900: #18181b;
  --ink-950: #09090b;

  /* island semantic tokens (spec §1) — for shell + new code */
  --app-bg: #08080a;
  --island: #111114;
  --island-2: #17171b;
  --card: #1d1d22;
  --line: rgba(255,255,255,.065);
  --line-strong: rgba(255,255,255,.12);
  --content: #e7e7ea;
  --content-2: #a3a3ad;
  --content-3: #74747e;
  --content-4: #55555e;
}

@theme inline {
  --color-ink-50: var(--ink-50);
  --color-ink-100: var(--ink-100);
  --color-ink-200: var(--ink-200);
  --color-ink-300: var(--ink-300);
  --color-ink-400: var(--ink-400);
  --color-ink-500: var(--ink-500);
  --color-ink-600: var(--ink-600);
  --color-ink-700: var(--ink-700);
  --color-ink-800: var(--ink-800);
  --color-ink-900: var(--ink-900);
  --color-ink-950: var(--ink-950);
  --color-app-bg: var(--app-bg);
  --color-island: var(--island);
  --color-island-2: var(--island-2);
  --color-card: var(--card);
  --color-line: var(--line);
  --color-line-strong: var(--line-strong);
  --color-content: var(--content);
  --color-content-2: var(--content-2);
  --color-content-3: var(--content-3);
  --color-content-4: var(--content-4);
}
```

- [ ] **Step 2: 验证 utilities 生效**

临时在 `frontend/App.tsx` 任意已渲染节点加 `className="bg-ink-900"`，运行：
```bash
cd frontend && npm run build 2>&1 | tail -2
grep -o "bg-ink-900" dist/assets/*.css | head -1
```
Expected: build 成功且 dist css 含 `bg-ink-900` 规则。验证后**撤销临时改动**。

- [ ] **Step 3: tsc + vitest 基线**

```bash
npx tsc --noEmit 2>&1 | wc -l   # 期望 140
npx vitest run 2>&1 | grep "Tests "  # 期望 929 passed
```

- [ ] **Step 4: Commit**

```bash
git add frontend/index.css
git commit -m "feat(theme): ink neutral ladder + island semantic tokens (v2 P1a Task 1)"
```

---

### Task 2: zinc→ink codemod 脚本

**Files:**
- Create: `frontend/scripts/migrate-ink.mjs`
- Test: 内置 `--self-test`（fixture 断言，零依赖）

- [ ] **Step 1: 写脚本（完整内容）**

```js
#!/usr/bin/env node
// zinc→ink codemod (island redesign v2 P1a).
// Mechanically renames Tailwind `*-zinc-N` utilities to `*-ink-N` inside
// string literals of .tsx/.ts files. Variants (hover:, md:, group-hover:)
// and opacity modifiers (/50) are untouched because we only rewrite the
// `zinc-<shade>` segment when preceded by a known utility prefix.
//
// Usage:
//   node scripts/migrate-ink.mjs --dry <dir|file>...   # report only
//   node scripts/migrate-ink.mjs <dir|file>...         # rewrite in place
//   node scripts/migrate-ink.mjs --self-test
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

// Every Tailwind color-utility prefix observed in this repo. `zinc-` only
// rewrites when attached to one of these, so prose like "zinc-alloy" is safe.
const PREFIXES = [
  'bg', 'text', 'border', 'border-t', 'border-b', 'border-l', 'border-r',
  'border-x', 'border-y', 'ring', 'ring-offset', 'divide', 'placeholder',
  'from', 'via', 'to', 'shadow', 'outline', 'decoration', 'accent', 'caret',
  'fill', 'stroke',
];
const RE = new RegExp(
  `(\\b(?:${PREFIXES.map(p => p.replace(/-/g, '\\-')).join('|')})-)zinc(-(?:50|100|200|300|400|500|600|700|800|900|950)\\b)`,
  'g',
);

function transform(src) {
  let count = 0;
  const out = src.replace(RE, (_, pre, shade) => { count++; return `${pre}ink${shade}`; });
  return { out, count };
}

function* walk(path) {
  const st = statSync(path);
  if (st.isFile()) { yield path; return; }
  for (const name of readdirSync(path)) {
    if (name === 'node_modules' || name === 'dist') continue;
    yield* walk(join(path, name));
  }
}

function selfTest() {
  const cases = [
    ['bg-zinc-800', 'bg-ink-800'],
    ['hover:bg-zinc-800/50', 'hover:bg-ink-800/50'],
    ['md:text-zinc-400 group-hover:border-zinc-700/60', 'md:text-ink-400 group-hover:border-ink-700/60'],
    ['from-zinc-900 via-zinc-800 to-zinc-950', 'from-ink-900 via-ink-800 to-ink-950'],
    ['divide-zinc-800 ring-zinc-700 placeholder-zinc-600', 'divide-ink-800 ring-ink-700 placeholder-ink-600'],
    ['bg-zinc-800', 'bg-ink-800'],
    // must NOT touch:
    ['zinc-800', 'zinc-800'],                  // bare token (no utility prefix)
    ['bg-gray-800 text-indigo-400', 'bg-gray-800 text-indigo-400'],
    ['bgzinc-800', 'bgzinc-800'],              // not a word boundary
  ];
  let failed = 0;
  for (const [input, expected] of cases) {
    const { out } = transform(input);
    if (out !== expected) { failed++; console.error(`FAIL: ${input} -> ${out} (want ${expected})`); }
  }
  console.log(failed === 0 ? `self-test OK (${cases.length} cases)` : `self-test FAILED (${failed})`);
  process.exit(failed === 0 ? 0 : 1);
}

const args = process.argv.slice(2);
if (args.includes('--self-test')) selfTest();
const dry = args.includes('--dry');
const targets = args.filter(a => !a.startsWith('--'));
if (targets.length === 0) { console.error('usage: migrate-ink.mjs [--dry] <dir|file>...'); process.exit(1); }

let totalFiles = 0, totalRepl = 0;
for (const target of targets) {
  for (const file of walk(target)) {
    if (!['.tsx', '.ts'].includes(extname(file))) continue;
    const src = readFileSync(file, 'utf8');
    const { out, count } = transform(src);
    if (count === 0) continue;
    totalFiles++; totalRepl += count;
    if (dry) console.log(`${file}: ${count}`);
    else writeFileSync(file, out);
  }
}
console.log(`${dry ? '[dry] ' : ''}${totalRepl} replacements in ${totalFiles} files`);
```

- [ ] **Step 2: self-test 必须先红后绿的对照** — 直接运行：

```bash
cd frontend && node scripts/migrate-ink.mjs --self-test
```
Expected: `self-test OK (9 cases)`（若 FAIL，修正 RE 再跑）。

- [ ] **Step 3: 全仓 dry-run 报告（不改文件）**

```bash
node scripts/migrate-ink.mjs --dry components pages App.tsx contexts | tail -3
```
Expected: 总替换数 ≈ 5,900±（与 `grep -rno "bg-zinc-\|text-zinc-\|border-zinc-" … | wc -l` 同量级；codemod 还覆盖 ring/from/divide 等所以可能略多）。

- [ ] **Step 4: Commit**

```bash
git add frontend/scripts/migrate-ink.mjs
git commit -m "feat(theme): zinc->ink codemod with self-test (v2 P1a Task 2)"
```

---

### Task 3: 试点批 — components/TaskCenter

**Files:**
- Modify: `frontend/components/TaskCenter/**`（约 233 处）

- [ ] **Step 1: 执行**

```bash
cd frontend && node scripts/migrate-ink.mjs components/TaskCenter
```

- [ ] **Step 2: 残留为零**

```bash
grep -rno "zinc-" components/TaskCenter --include="*.tsx" --include="*.ts" | grep -v "gray-" | wc -l
```
Expected: 0（若 >0，逐条看是否是 PREFIXES 没覆盖的 utility — 把前缀补进脚本、重跑 self-test、再执行）。

- [ ] **Step 3: 基线验证**

```bash
npx tsc --noEmit 2>&1 | wc -l        # 140
npx vitest run 2>&1 | grep "Tests "  # 929 passed
npm run build 2>&1 | tail -1         # 成功
```

- [ ] **Step 4: 视觉 no-op 抽查** — `npm run dev` 起本地（端口见 .worktree.env），打开 Task Center 面板对照线上：颜色应逐像素一致（值相同）。无法起 dev 时跳过，依赖「值相同」的构造性保证。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/TaskCenter
git commit -m "refactor(theme): TaskCenter zinc->ink (v2 P1a pilot batch)"
```

---

### Task 4-9: 批量迁移（每批 = Task 3 的同款五步）

每批执行与 Task 3 完全相同的 Step 1-5（替换目标路径与 commit message），按以下顺序：

- [ ] **Task 4**: `components/AILibrary`（~630）
  `node scripts/migrate-ink.mjs components/AILibrary` → `git commit -m "refactor(theme): AILibrary zinc->ink"`
- [ ] **Task 5**: `components/Todolist components/project components/Workforce components/chat`（~490）
  → `git commit -m "refactor(theme): Todolist/project/Workforce/chat zinc->ink"`
- [ ] **Task 6**: `components/resources components/DownloadsView components/filters components/EagleTagPicker`（~370）
  → `git commit -m "refactor(theme): resources/downloads/filters/tagpicker zinc->ink"`
- [ ] **Task 7**: `pages`（~376+admin112）
  → `git commit -m "refactor(theme): pages zinc->ink"`
- [ ] **Task 8**: `components`（根级剩余 ~2,500 — 此时子目录已清，脚本会跳过无匹配文件）
  → `git commit -m "refactor(theme): remaining components zinc->ink"`
- [ ] **Task 9**: `App.tsx contexts` + **全仓清零审计**：

```bash
node scripts/migrate-ink.mjs App.tsx contexts
grep -rno "\b\(bg\|text\|border\|ring\|divide\|placeholder\|from\|via\|to\|shadow\|outline\)-zinc-" components pages App.tsx contexts --include="*.tsx" --include="*.ts" | wc -l
```
Expected: 0。然后 tsc/vitest/build 三连 + `git commit -m "refactor(theme): final zinc->ink sweep, repo clean"`。

每批之间若 vitest 出现 fail：先看失败测试是否断言了 zinc 类名字符串（测试断言 className 含 `zinc-` 的要同步改成 `ink-`，属于迁移的一部分，不是功能改动）。

---

### Task 10: Light 主题 palette

**Files:**
- Modify: `frontend/index.css`（Task 1 块之后追加）

- [ ] **Step 1: 追加 light 值块**

```css
/* Light theme — ladder inverted + tuned (spec §5.4). Semantic colors
   (indigo/green/amber/red/violet) intentionally unchanged. */
[data-theme="light"] {
  --ink-50:  #18181b;
  --ink-100: #27272a;
  --ink-200: #3f3f46;
  --ink-300: #52525b;
  --ink-400: #71717a;
  --ink-500: #a1a1aa;
  --ink-600: #d4d4d8;
  --ink-700: #e4e4e7;
  --ink-800: #f0f0f2;
  --ink-900: #f7f7f8;
  --ink-950: #fcfcfd;

  --app-bg: #f4f4f6;
  --island: #ffffff;
  --island-2: #f4f4f5;
  --card: #fafafa;
  --line: rgba(0,0,0,.08);
  --line-strong: rgba(0,0,0,.16);
  --content: #1c1c21;
  --content-2: #52525b;
  --content-3: #8b8b94;
  --content-4: #b0b0b8;
}
```

- [ ] **Step 2: 手动验证** — 浏览器 DevTools 对任意页面执行 `document.documentElement.dataset.theme='light'`：页面应整体翻白、文字可读（个别对比度问题记 TODO 列表，不阻塞本 task —— 精调在 Task 11 切换器可用后统一做一轮）。

- [ ] **Step 3: Commit**

```bash
git add frontend/index.css
git commit -m "feat(theme): light palette via [data-theme=light] (v2 P1a Task 10)"
```

---

### Task 11: ThemeContext + Settings 三态切换

**Files:**
- Create: `frontend/contexts/ThemeContext.tsx`
- Modify: `frontend/components/SettingsView.tsx`（General 区，用 `grep -n "General" components/SettingsView.tsx` 定位插入点）
- Modify: `frontend/App.tsx`（Provider 挂载，包在现有最外层 Provider 内侧）
- 翻译: `frontend/public/locales/{en,zh}.json` 增 `settings.theme.{label,system,light,dark}`

- [ ] **Step 1: 写 ThemeContext（完整内容）**

```tsx
import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';

// Island redesign D11 — three-state theme. 'system' follows
// prefers-color-scheme; resolved value is written to <html data-theme>.
export type ThemePreference = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'mediahub.theme';

interface ThemeContextValue {
  preference: ThemePreference;
  resolved: 'light' | 'dark';
  setPreference: (p: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function systemTheme(): 'light' | 'dark' {
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [preference, setPreferenceState] = useState<ThemePreference>(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved === 'light' || saved === 'dark' || saved === 'system' ? saved : 'system';
  });
  const [resolved, setResolved] = useState<'light' | 'dark'>(() =>
    preference === 'system' ? systemTheme() : preference,
  );

  const setPreference = useCallback((p: ThemePreference) => {
    setPreferenceState(p);
    localStorage.setItem(STORAGE_KEY, p);
  }, []);

  useEffect(() => {
    const apply = () => {
      const next = preference === 'system' ? systemTheme() : preference;
      setResolved(next);
      document.documentElement.dataset.theme = next;
    };
    apply();
    if (preference !== 'system') return;
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    mq.addEventListener('change', apply);
    return () => mq.removeEventListener('change', apply);
  }, [preference]);

  return (
    <ThemeContext.Provider value={{ preference, resolved, setPreference }}>
      {children}
    </ThemeContext.Provider>
  );
};

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
```

- [ ] **Step 2: 写测试** `frontend/contexts/ThemeContext.test.tsx`

```tsx
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { ThemeProvider, useTheme } from './ThemeContext';

function Probe() {
  const { preference, resolved, setPreference } = useTheme();
  return (
    <div>
      <span data-testid="pref">{preference}</span>
      <span data-testid="resolved">{resolved}</span>
      <button onClick={() => setPreference('light')}>go-light</button>
    </div>
  );
}

describe('ThemeContext', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: false, // system = dark
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
  });

  it('defaults to system and resolves dark, writing data-theme', () => {
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByTestId('pref').textContent).toBe('system');
    expect(screen.getByTestId('resolved').textContent).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('setPreference(light) persists and flips data-theme', () => {
    render(<ThemeProvider><Probe /></ThemeProvider>);
    fireEvent.click(screen.getByText('go-light'));
    expect(localStorage.getItem('mediahub.theme')).toBe('light');
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});
```

运行：`npx vitest run contexts/ThemeContext.test.tsx` → Expected: 先因文件不存在 FAIL（红），实现 Step 1 后 PASS（绿）。

- [ ] **Step 3: App.tsx 挂 Provider** — 在最外层现有 Provider 栈内侧包一层 `<ThemeProvider>…</ThemeProvider>`（`grep -n "Provider" App.tsx | head` 找栈位置，放最内不影响其他 context）。

- [ ] **Step 4: SettingsView 三态选择器** — 在 General 区追加（样式跟随相邻设置项的现有模式；图标用 lucide `Monitor`/`Sun`/`Moon`，D4 禁 emoji）：

```tsx
{/* Theme (island redesign D11) */}
<div className="flex items-center justify-between py-3">
  <span className="text-sm text-ink-300">{t('settings.theme.label', 'Theme')}</span>
  <div className="flex gap-1 bg-ink-900 border border-ink-800 rounded-lg p-0.5">
    {(['system', 'light', 'dark'] as const).map((p) => (
      <button
        key={p}
        onClick={() => setPreference(p)}
        className={`px-3 py-1 text-xs rounded-md transition-colors ${
          preference === p ? 'bg-ink-800 text-ink-100' : 'text-ink-500 hover:text-ink-300'
        }`}
      >
        {t(`settings.theme.${p}`, p === 'system' ? 'System' : p === 'light' ? 'Light' : 'Dark')}
      </button>
    ))}
  </div>
</div>
```

（`const { preference, setPreference } = useTheme();` 加在组件顶部；import 路径 `../contexts/ThemeContext`。）

- [ ] **Step 5: i18n** — en: `{"theme":{"label":"Theme","system":"System","light":"Light","dark":"Dark"}}`；zh: `{"theme":{"label":"主题","system":"跟随系统","light":"浅色","dark":"深色"}}`（挂在 `settings` 命名空间下，用既有 python json merge 模式写入）。

- [ ] **Step 6: 全量验证 + Commit**

```bash
npx tsc --noEmit 2>&1 | wc -l && npx vitest run 2>&1 | grep "Tests "
git add frontend/contexts/ThemeContext.tsx frontend/contexts/ThemeContext.test.tsx frontend/App.tsx frontend/components/SettingsView.tsx frontend/public/locales
git commit -m "feat(theme): ThemeProvider + Settings three-state switcher (v2 P1a Task 11)"
```

---

### Task 12: 浅色精调 + 守卫 + ship

- [ ] **Step 1: 浅色走查** — 切 Light，过主要页面（资源库/Task Center/Settings/详情页），把对比度问题（白底白字、透明度黑阴影等）逐个修在 token 值层（只调 `[data-theme="light"]` 块的值，不动组件）。

- [ ] **Step 2: 防回归守卫** — `frontend/scripts/check-no-zinc.sh`：

```bash
#!/usr/bin/env bash
# island redesign: zinc utilities are forbidden after the ink migration.
set -e
n=$(grep -rno "\b\(bg\|text\|border\|ring\|divide\|placeholder\|from\|via\|to\)-zinc-" \
  components pages App.tsx contexts --include="*.tsx" --include="*.ts" | wc -l | tr -d ' ')
if [ "$n" != "0" ]; then
  echo "FOUND $n forbidden *-zinc-* usages (use *-ink-* / semantic tokens):"
  grep -rno "\b\(bg\|text\|border\)-zinc-" components pages App.tsx contexts --include="*.tsx" | head -20
  exit 1
fi
echo "no zinc utilities — OK"
```

并在 CI 前端 job 里（`.github/workflows/` 的 Frontend Build job steps）加一步 `bash scripts/check-no-zinc.sh`（working-directory: frontend）。

- [ ] **Step 3: 版本 + PR** — 读 origin/master 当前版本再 bump（并行 session 撞号风险）；PR 描述附 D12 声明（纯 className 机械替换 + 新增 ThemeContext，零行为改动）+ 标准 ship 链（public→CI→merge→Vercel Production 行验证→线上版本 curl→inflight=0 再 private）。

---

## Self-Review 记录

- **Spec 覆盖**：D11 双主题（Task 1/10/11）✅ token 语义命名挂 [data-theme] ✅ 浅色明度镜像+语义色不变（Task 10）✅ 5,911 处迁移（Task 3-9）✅ D4 无 emoji（Task 11 用 lucide）✅ D12（机械替换+附加设置项，零功能变化）✅。**降级说明**：spec §5.4 期望组件直接用语义 4 档 content token —— 存量迁移采用 ink 阶梯（机械可证 no-op），语义 token 供新代码/shell 使用；两者同源于 [data-theme] 变量，双主题目标不受影响。已在 Task 1 注释中写明。
- **Placeholder 扫描**：无 TBD/TODO/“similar to”；Task 4-9 显式给出各批路径与 commit message，五步流程在 Task 3 完整展开一次属同一可复用程序（路径参数化），每批命令均已写出。
- **类型/命名一致性**：`ThemePreference`/`useTheme`/`setPreference` 在 Task 11 各步一致；codemod 函数名 `transform`/`walk` 内聚单文件。
- **风险**：① 测试断言 className 含 zinc → Task 3-9 的修复指引已写。② `@theme inline` + 透明度修饰符：v4 对 var 颜色用 color-mix 生成 `/50`，Task 1 Step 2 的 build 验证覆盖。③ `--color-line` 是 rgba 值，`border-line/50` 之类二次透明慎用（新代码注意，存量不涉及）。
