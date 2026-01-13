# Progress Recorder

## 📋 功能概述
Progress Recorder 是一个智能进度管理技能，专门解决长时间对话中因上下文窗口限制导致的任务信息丢失问题。

**核心机制**：完全依赖 **Claude 的语义理解能力**来解析对话内容、提取关键任务和决策信息、进行智能去重和合并。不依赖正则表达式或字符串相似度算法，而是真正理解对话的深层含义和上下文关联。

**核心价值**：即使对话重启或上下文丢失，你的项目进度、关键决策、核心约束也能被完整保留和追溯。

## 🎯 使用场景
- **长期项目管理**：跨多天、多次对话的项目开发，需要持续跟踪任务进度
- **上下文保护**：防止因对话过长导致的早期决策和任务信息丢失
- **团队协作**：生成结构化的进度文档，便于团队成员了解项目状态
- **知识沉淀**：自动记录技术决策、问题解决方案和重要结论
- **进度复盘**：通过归档文件回顾项目历史，分析时间线和决策演进

## 🚀 快速开始

### 触发 Skill
直接对 Claude 说以下任一句：
- "更新进度" / "update progress"
- "记录当前任务" / "record current tasks"
- "持久化进度信息" / "persist progress"
- "归档已完成任务" / "archive completed tasks"
- 或包含"决定"、"必须"、"完成"等关键词的对话

### 示例对话

**场景 1：首次使用 + 高置信判定**
```
User: 我们决定使用 React 18 开发前端，必须支持 IE11。
      需要实现用户登录功能。

Claude: [识别到 Progress Recorder]
正在语义分析对话内容...

提取到：
- Decisions: 决定使用 React 18 开发前端
- Pinned: 必须支持 IE11
- TODO: [P1][OPEN][#1] 实现用户登录功能

✅ progress.md 已创建并更新完成

📝 Changes:
- New Decisions: 1
- New Constraints: 1
- Added: 1 new task
```

**场景 2：状态更新 + 语义去重**
```
User: 登录功能完成了，现在开始做权限模型。
      另外，我们也要实现用户登录（注：重复提及）

Claude: [识别到 Progress Recorder]
正在智能合并...

检测到：
- "登录功能完成了" → [#1] 移至 Done
- "做权限模型" → [#1] 状态变更为 DOING
- "实现用户登录" → 与 #1 语义相同，已去重

✅ progress.md 已更新完成

📊 Current Status:
- Done: 1 task
- In Progress: 1 task
```

**场景 3：低置信降级**
```
User: 我们可能需要支持暗色模式，也许用 Tailwind CSS 比较好。

Claude: [识别到 Progress Recorder]
检测到低置信表达（"可能"、"也许"）

提取到：
- Notes: Needs-Confirmation: 可能需要支持暗色模式
- Notes: Needs-Confirmation: 也许用 Tailwind CSS

✅ progress.md 已更新完成

⚠️ Attention:
- Needs-Confirmation: 2 items (请明确确认后再记录为正式决策)
```

**场景 4：自动归档**
```
User: 更新进度并归档旧任务

Claude: [识别到 Progress Recorder]
检测到 Notes + Done 超过 100 条，触发归档...
正在归档历史记录...

✅ progress.md 已更新完成

📝 Changes:
- Archived: 45 tasks to progress.archive.md
- Active tasks remaining: 12
- File size reduced: 85KB → 22KB
```

## 📁 输入/输出

### 输入
- **对话内容**：Claude 自动分析当前对话上下文（必需）
- **现有 progress.md**：如果存在，读取并增量合并（可选）
- **自定义路径**：可指定进度文件路径，如 `docs/progress.md`（可选）

### 输出
- **progress.md**：主进度文件，包含 8 个标准区块：
  * **Pinned**：受保护的核心约束（如"必须支持 IE11"）
  * **Decisions**：受保护的决策历史（按时间追加，永不修改）
  * **TODO**：待办任务（带 ID、优先级、状态）
  * **In Progress**：进行中的任务
  * **Done**：最近完成的任务（附带证据链接）
  * **Risks & Assumptions**：风险和假设
  * **Notes**：简要要点和待确认事项
  * **Context Index**：归档文件索引

- **progress.archive.md**：归档文件（达到阈值时生成），包含：
  * 历史 Notes 和 Done 条目
  * 归档时间戳和分隔符
  * 只增不删的完整历史记录

- **更新摘要**：控制台输出，显示本次变更统计和当前状态

## 📊 Progress.md 文件格式

完整的 progress.md 文件结构（基于 subagent 成熟设计）：

```markdown
# Project: 我的项目

_Last updated: 2026-01-11 10:30_

## Pinned（仅高置信"必须遵守"写入；受保护不可修订）
- 必须支持 IE11 以上浏览器
- 不能使用 GPL 协议的第三方库
- 要求 99.9% 可用性

## Decisions（按时间顺序追加，历史不可改）
- 2026-01-10: 决定使用 React 18 开发前端（理由：支持并发渲染）
- 2026-01-11: 选择 PostgreSQL 作为主数据库（理由：需要 JSON 字段）

## TODO（权威待办清单）
- [P0][OPEN][#1] 实现用户登录功能（Owner：张三，Context：src/auth/）
- [P1][OPEN][#2] 添加单元测试覆盖（Owner：李四）

## In Progress
- [P0][DOING][#3] 设计数据库 schema（Owner：王五，Context：docs/db.md）

## Done（最近完成的放前面）
- 2026-01-11: [#4] 完成项目初始化（evidence: commit abc123）
- 2026-01-10: [#5] 搭建开发环境（evidence: docs/setup.md）

## Risks & Assumptions
- Risk：IE11 兼容可能导致开发成本增加（Mitigation：使用 polyfill）
- Assumption：用户量不超过 10 万（Confidence：Med）

## Notes（简要要点）
- 2026-01-11: 需要确认 API 限流策略
- Needs-Confirmation：是否需要支持移动端？

## Context Index（轻量索引）
- Archive：./progress.archive.md
```

## 🔧 核心机制

### 1. 高置信判定机制

Progress Recorder 的核心创新是**高置信判定**，确保只有明确的承诺才会写入受保护区块：

```
强承诺语言 → 写入受保护区块（Pinned/Decisions）
弱化语言   → 降级到 Notes（标注 Needs-Confirmation）

示例：
"必须使用 React"     → Pinned（高置信）
"建议使用 React"     → Notes: Needs-Confirmation（低置信）
"决定用 PostgreSQL"  → Decisions（高置信）
"可能用 PostgreSQL"  → Notes: Needs-Confirmation（低置信）

弱化词列表：
可能、也许、大概、似乎、建议、考虑、或许、可以、想
```

### 2. 受保护区块机制

防止关键信息被意外修改或删除：

```
┌──────────────────────────────────────────────────┐
│  Pinned/Decisions │  【受保护】只增不改             │
│  - 一旦写入永不自动修改                            │
│  - 冲突时在 Notes 中提醒，不覆盖历史                │
├───────────────────┼──────────────────────────────┤
│  TODO             │  【半保护】可更新状态，ID 永不复用│
│  - 状态可流转：OPEN → DOING → DONE                │
│  - ID 单调递增，删除的 ID 不再分配给新任务           │
├───────────────────┼──────────────────────────────┤
│  Notes/Done       │  【可归档】超阈值迁移到 archive  │
│  - 保留最近 50 条，其余归档                        │
│  - archive 文件只增不删，保持完整历史               │
└──────────────────────────────────────────────────┘
```

### 3. TODO ID 管理系统

每个 TODO 都有唯一的递增 ID，支持完整的生命周期追踪：

```
格式：[优先级][状态][#ID] 任务描述（Owner：可选，Context：可选）

示例：
- [P0][OPEN][#1] 修复登录 bug（Owner：张三，Context：src/auth/login.ts）
- [P1][DOING][#2] 优化数据库查询（Owner：李四）
- [P2][OPEN][#3] 更新文档

优先级：
P0 - 紧急（必须立即处理）
P1 - 重要（默认优先级）
P2 - 普通（可延后处理）

状态流转：
OPEN → DOING → DONE
```

### 4. 语义去重

完全依赖 Claude 的语义理解能力，不使用字符串相似度算法：

```
示例 1：同义任务合并
现有："添加用户登录功能"
新提："实现登录模块"
→ Claude 判断：语义相同，更新原条目

示例 2：不同任务识别
现有："添加用户登录功能"
新提："优化登录速度"
→ Claude 判断：不同任务，添加新条目

示例 3：隐含任务识别
对话："登录这块体验不太好，后面优化一下"
→ Claude 识别：TODO: 优化登录用户体验
```

### 5. 证据追踪

为完成的任务附加证据链接，便于追溯和验证：

```
Done 条目格式：
YYYY-MM-DD: [#ID] 任务描述（evidence: 证据链接）

证据类型：
- commit hash：evidence: commit abc123
- PR 链接：evidence: PR #42
- 文件路径：evidence: src/auth/login.ts
- 文档链接：evidence: docs/api.md

示例：
- 2026-01-11: [#4] 实现登录功能（evidence: commit abc123, PR #42）
- 2026-01-11: [#5] 修复内存泄漏（evidence: src/utils/memory.ts）
```

## 🔧 智能归档机制

### 归档触发条件

Progress Recorder 会在以下情况下建议归档：

1. **数量触发**：Notes + Done 总数超过 100 条
2. **手动触发**：用户明确要求（如"归档这个月的进度"）
3. **阶段触发**：项目进入新阶段/里程碑（如"完成认证模块，开始支付模块"）

### 归档是语义总结，不是文本搬运

**错误理解**：把旧条目复制粘贴到 archive.md ❌

**正确理解**：将零散任务总结为结构化的里程碑 ✅

```
输入：50 条零散的 Done 任务
- 2026-01-05: [#1] 实现 JWT token 生成
- 2026-01-06: [#2] 添加 token 过期检查
- 2026-01-07: [#3] 实现 refresh token 机制
... (共 50 条)

输出：结构化的里程碑总结
## [Phase 1.1] 用户认证模块 - 2026-01

### 📋 概述
完成用户认证系统 v1.0，包括 JWT 认证、用户管理、权限系统

### ✅ 主要成果
- JWT 认证机制：token 生成、过期检查、refresh 机制
- 用户管理：登录、注册、密码重置、邮箱验证
- 权限系统：RBAC 模型，支持角色和权限分配

### 🎯 关键决策
- 选择 JWT 而非 Session（理由：无状态，易扩展）
- 使用 PostgreSQL（理由：需要 JSON 字段支持）

### 📚 经验教训
- 成功经验：提前设计好权限模型，避免后期重构
- 改进空间：IE11 兼容耗时，下次提前准备 polyfill
- 避免踩坑：JWT secret 必须足够长（至少 256 位）

### 📊 数据统计
- 完成任务：18 个
- 新增代码：约 3000 行
- 时间跨度：2026-01-02 ~ 2026-01-20
```

这种**语义总结能力**只有 Claude 能做到，脚本无法实现！

### 如何触发归档

直接对 Claude 说：
- "归档这个月的进度"
- "总结一下认证模块的开发历程"
- "把已完成的任务归档，保持 progress.md 简洁"

Claude 会自动：
1. 识别归档范围（按时间/模块/里程碑分组）
2. 深度理解和提炼（总结成果、决策、教训）
3. 生成结构化的里程碑文档
4. 清理 progress.md（保留最近 30 天或 50 条）
5. 更新 progress.archive.md（最新归档在最上面）

## 📚 相关文档
- [SKILL.md](SKILL.md) - 完整的技能定义和执行流程
- [EXAMPLES.md](EXAMPLES.md) - 详细使用示例
- [templates/progress_template.md](templates/progress_template.md) - 进度文件模板
- [templates/archive_template.md](templates/archive_template.md) - 归档文件模板

## 🐛 故障排除

### 问题1：生成的 progress.md 格式混乱
**症状**：文件中出现重复的标题、缩进不一致、列表符号错误
**原因**：可能是现有文件格式不符合标准模板
**解决**：
1. 检查现有 progress.md 是否符合标准格式（参考模板）
2. 如果格式严重错误，从模板重新生成：`cp .claude/skills/progress_recorder/templates/progress_template.md progress.md`
3. 请 Claude 重新解析对话并更新进度

### 问题2：任务被错误地合并
**症状**：明明是不同的任务，却被合并为一个
**原因**：语义理解可能判断为同一任务
**解决**：
1. 在任务描述中添加更多区分性细节
2. 使用明确的标识符或前缀，如 `[AUTH] 登录功能`、`[PERF] 登录优化`
3. 手动编辑 progress.md，拆分为两个独立任务

### 问题3：归档后找不到历史任务
**症状**：某些已完成的任务在 progress.md 和 progress.archive.md 中都找不到
**原因**：归档时可能遗漏某些条目
**解决**：
1. 检查 `.backup/` 目录，查找归档前的备份文件
2. 查看 progress.archive.md，按 Phase 查找对应时期的归档
3. 使用 Git 历史恢复：`git log -- progress.md`
4. 如果确认遗漏，请 Claude 重新总结那段时期的工作并补充到 archive.md

### 问题4：冲突标记一直存在
**症状**：progress.md 中出现 `⚠️ CONFLICT:` 标记，不知道如何处理
**原因**：检测到矛盾信息，需要人工确认
**解决**：
1. 查看 Notes 中的冲突描述，理解矛盾点
2. 决定采用哪个版本（如旧决策 vs 新决策）
3. 手动编辑 progress.md：
   - 如果新信息正确，更新 Pinned/Decisions
   - 如果旧信息正确，删除 Notes 中的冲突提示
4. 删除 `⚠️ CONFLICT:` 标记
5. 再次运行 Progress Recorder 确认冲突已解决

### 问题5：低置信信息太多
**症状**：Notes 中积累了大量 "Needs-Confirmation" 条目
**原因**：对话中使用了很多弱化词（"可能"、"也许"等）
**解决**：
1. 定期 review Notes 中的待确认事项
2. 对于确认的事项，明确表达："确定使用 XX"、"必须支持 YY"
3. 让 Claude 将确认的事项升级到 Pinned/Decisions
4. 删除不需要的低置信条目

## 💡 使用技巧

### 技巧 1：结合 Git 使用
定期提交 progress.md 到 Git，可以追溯完整的项目演进历史：
```bash
git add progress.md progress.archive.md
git commit -m "chore: update project progress [auto]"
```

### 技巧 2：自定义分类结构
编辑 `templates/progress_template.md`，添加适合你项目的分类：
```markdown
## 🎨 Frontend Tasks
## ⚙️ Backend Tasks
## 🗄️ Database Tasks
## 📱 Mobile Tasks
```

### 技巧 3：使用明确的表达
为了确保信息被正确分类到受保护区块，使用明确的强承诺语言：
```
✅ 好的表达：
"决定使用 React 18"
"必须支持 Safari"
"完成了登录功能"

❌ 模糊的表达：
"可能用 React"
"建议支持 Safari"
"登录差不多了"
```

### 技巧 4：定期 review Notes
每周检查一次 Notes 中的待确认事项，及时转化为正式决策或删除：
```
User: review progress.md 中的 Needs-Confirmation 事项，
      "支持暗色模式"确定要做，"使用 Tailwind"也确定了。

Claude: 正在升级待确认事项...
- "支持暗色模式" → TODO: [P1][OPEN][#6] 实现暗色模式
- "使用 Tailwind" → Decisions: 2026-01-11: 决定使用 Tailwind CSS
```

### 技巧 5：利用 TODO ID 追踪
在代码注释、commit message、PR 中引用 TODO ID，建立完整的追溯链：
```bash
git commit -m "feat: implement login (#1)"
# PR description
Closes #1 - 实现用户登录功能
```

### 技巧 6：定期归档，保持精简
每月或每个里程碑结束时，主动请 Claude 归档：
```
User: 认证模块开发完成了，归档一下这个月的进度吧

Claude: 正在分析 2026-01 的开发历史...
识别到主题：用户认证模块开发
关键成果：JWT 认证、用户管理、权限系统
正在生成里程碑总结...

✅ Archive Completed - [Phase 1.1] 用户认证模块 - 2026-01
```

### 技巧 7：利用归档做复盘
archive.md 是绝佳的项目复盘材料：
```
User: 回顾一下过去 3 个月的开发历程，有哪些经验教训？

Claude: 根据 progress.archive.md 中的 3 个 Phase 归档...

📚 经验教训总结：
**成功经验**：
- 提前设计架构避免重构（Phase 1.1）
- TDD 显著减少 bug（Phase 1.2）

**改进空间**：
- IE11 兼容性每次都耗时，应建立 polyfill 库（Phase 1.1, 1.3）
- 代码 review 不够及时，导致返工（Phase 1.2）
```

### 技巧 8：生成可视化报告
使用 Markdown 图表插件（如 Mermaid）在 progress.md 中嵌入甘特图：
```markdown
## 📊 Timeline
\`\`\`mermaid
gantt
    title Project Timeline
    section Authentication
    Login Implementation :done, 2026-01-10, 1d
    Permission Model   :active, 2026-01-11, 3d
\`\`\`
```

## 📝 更新日志
- v2.0.0 (2026-01-11): 重大架构升级
  - **移除所有脚本依赖**：完全基于 Claude 语义理解，不再依赖正则表达式或字符串相似度算法
  - **智能归档机制**：归档不再是简单搬运，而是语义总结和提炼，生成结构化的里程碑文档
  - **高置信判定**：区分强承诺语言（"必须"）和弱化语言（"可能"），防止把讨论当决策
  - **受保护区块**：Pinned 和 Decisions 永不自动修改，确保关键信息不丢失
  - **TODO ID 管理**：唯一递增 ID，支持完整生命周期追踪（OPEN → DOING → DONE）
  - **证据追踪**：Done 条目附带 commit/PR/文件路径，便于追溯
  - **采用 subagent 成熟设计**：8 个标准区块，结构化管理项目记忆
- v1.0.0 (2026-01-11): 初始版本（已废弃）
  - 基础的对话内容解析（依赖正则表达式）
  - 简单的语义去重（依赖字符串相似度）
  - 简单归档功能（仅复制粘贴）
