# seeds — 启动时装进库的 agent 与 skill

`SeedLoader`（`app/services/ai/runner/seed_loader.py`）每次进程启动跑一遍，把这个目录的内容 upsert 进 `ai_agents` / `skills` / `skill_files`。它是**幂等**的：每行存一个 `seed_hash`，磁盘内容没变就跳过写入（稳态大约 5 次 GET、0 次写）。

```
seeds/
  agents/<slug>/{IDENTITY,SOUL,AGENT}.md
  skills/<slug>/SKILL.md          (+ 可选 references/ scripts/ assets/)
```

目录名就是 slug，会原样进 `ai_agents.slug`（`script_ai` 的下划线与 `topic-scorer` 的连字符都是真实在用的，不是笔误）。

## Agent 的 frontmatter 契约

`AGENT.md` **可以**以 YAML frontmatter 开头。当前只认一个键：

```markdown
---
persistent: true
---

You are ...
```

| 键 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `persistent` | bool | `false` | 这个 agent 是 workforce worker，可作为 `Delegate` 的目标（`ai_agents.persistent`）。曾由迁移 162/163 设置，而那两条在 baseline watermark 之下、从未在这个库上跑过——所以改由 seed 声明、每次启动收敛 |

三条必须知道的规则：

1. **frontmatter 不进模型可见面。** 被识别的头会从 `agent_md` 里剥掉，模型看到的是正文。
2. **只有解析出「已知键」才剥。** 以 `---` 开头当**水平线**用的文件（或头里全是本 loader 不认识的键），整份文件原样进 `agent_md`，一个字都不丢。`frontmatter` 库本身会把水平线当成开栅栏并静默吃掉两条线之间的正文——这个守卫就是为它加的（Task 7a 评审 I4）。新增一个键要同时改 `seed_loader.FRONTMATTER_KEYS` 与本表。
3. **声明的值进 `seed_hash`。** 只翻标志、不动散文，下次启动照样会写库；反过来说，删掉一个键会让它收敛回默认值，而不是保留上次写进去的。

`IDENTITY.md` / `SOUL.md` **不解析 frontmatter**，整份都是散文。`IDENTITY.md` 的首句会被抽成 `ai_agents.description`。

## Skill 的 frontmatter

`SKILL.md` 的 frontmatter 一直是**必需**的，且键集不同（`name` / `description` / `category` / `icon` / `is_public`），整份 `metadata` 原样存进 `skills.frontmatter_json`。它与 agent 侧不是同一套规则。

## 改了 seed 之后

装载在启动时发生，所以本地要 `uv run uvicorn` 重启一次（或重建容器）才生效。生产靠部署重启自然收敛，不需要迁移。

分组（`ai_agents.agent_group`）不在 frontmatter 里，而在 `seed_loader.AGENT_GROUP_BY_SLUG`——那张表与本目录的锁步由 `tests/test_agent_group_seed.py` 钉住。
