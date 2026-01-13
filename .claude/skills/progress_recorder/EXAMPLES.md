# Progress Recorder - 使用示例

## 示例1：首次创建进度记录

### 背景
项目刚开始，与 Claude 讨论了需要实现的功能，想要记录下来避免遗忘。

### 对话内容
```
User: 我们需要实现以下功能：
1. 用户认证系统（JWT）
2. 数据库设计（PostgreSQL）
3. RESTful API 接口
4. 前端登录页面

请帮我记录进度。

Claude: [识别到 Progress Recorder]
正在解析对话内容...
提取到 4 个新任务
创建 progress.md 文件...
```

### 输出：progress.md
```markdown
# Project Progress

Last Updated: 2026-01-11 10:30

## 📋 Pending Tasks

- [ ] 用户认证系统（JWT） [Updated: 2026-01-11]
- [ ] 数据库设计（PostgreSQL） [Updated: 2026-01-11]
- [ ] RESTful API 接口 [Updated: 2026-01-11]
- [ ] 前端登录页面 [Updated: 2026-01-11]
```

### 执行过程
1. 解析对话，识别到 4 个任务项
2. 检测到 progress.md 不存在
3. 从模板创建新的 progress.md
4. 添加所有任务到待办列表
5. 返回成功消息

---

## 示例2：增量更新任务状态

### 背景
已经完成了用户认证系统，正在开发数据库，想要更新进度。

### 对话内容
```
User: JWT 认证已经完成了，数据库设计也做好了，正在写 API 接口。更新一下进度。

Claude: [识别到 Progress Recorder]
读取现有 progress.md...
检测到任务状态变更：
- "用户认证系统（JWT）" → 已完成 ✓
- "数据库设计（PostgreSQL）" → 已完成 ✓
- "RESTful API 接口" → 进行中 ⏳

✅ progress.md 已更新完成

📊 Current Status:
- Completed: 2 tasks
- In progress: 1 task
- Pending: 1 task
```

### 输出：更新后的 progress.md
```markdown
# Project Progress

Last Updated: 2026-01-11 14:15

## 🔧 In Progress

- [ ] RESTful API 接口 [In Progress] [Updated: 2026-01-11]

## 📋 Pending Tasks

- [ ] 前端登录页面 [Updated: 2026-01-11]

## ✅ Recently Completed

- [x] 用户认证系统（JWT） [Completed: 2026-01-11 14:15]
- [x] 数据库设计（PostgreSQL） [Completed: 2026-01-11 14:15]
```

### 执行过程
1. 解析对话，识别到 3 个状态变更
2. 读取现有 progress.md
3. 使用语义匹配找到对应任务
4. 更新任务状态和时间戳
5. 重新组织任务分类
6. 写入更新后的文件

---

## 示例3：处理重复和冲突

### 背景
在不同对话中多次提到相同任务，或者状态信息冲突。

### 对话内容
```
User: 我们需要添加用户注册功能，还有邮箱验证。
对了，用户注册这块也要做。

Claude: [识别到 Progress Recorder]
正在合并任务...

检测到重复任务：
- "用户注册功能" 与已有任务 "添加用户注册功能" 相似度: 0.92
  → 已自动合并，避免重复

新增任务：
- 邮箱验证

✅ progress.md 已更新完成
```

### 输出：合并结果
```markdown
## 📋 Pending Tasks

- [ ] 用户注册功能 [Updated: 2026-01-11]
- [ ] 邮箱验证 [Updated: 2026-01-11]
```

### 执行过程
1. 提取到 3 个任务描述
2. 语义分析检测到 "用户注册功能" 和 "添加用户注册功能" 相似度 > 0.85
3. 自动合并重复任务
4. 添加新的独特任务 "邮箱验证"
5. 更新文件

---

## 示例4：自动归档

### 背景
项目进行了 2 个月，progress.md 已经有 600 多行，包含很多已完成的历史任务。

### 对话内容
```
User: 更新进度，把已完成的任务归档一下。

Claude: [识别到 Progress Recorder]
检测到归档阈值已达到：
- 总行数: 627 / 500 (超出)
- 已完成任务: 58 / 50 (超出)
- 文件大小: 95KB / 100KB

开始归档...
📦 备份已创建: .backup/progress_20260111_163022.md
正在迁移已完成任务到 progress.archive.md...

✅ 归档完成

📝 Changes:
- Archived: 48 tasks
- Active tasks remaining: 15
- File size reduced: 95KB → 28KB
```

### 输出：progress.archive.md (新增内容)
```markdown
---
## Archived: 2026-01-11 16:30
Archived 48 tasks
---

- [x] 用户认证系统（JWT） [Completed: 2026-01-11]
- [x] 数据库设计（PostgreSQL） [Completed: 2026-01-11]
- [x] RESTful API 接口 [Completed: 2026-01-15]
- [x] 前端登录页面 [Completed: 2026-01-20]
...（更多已完成任务）
```

### 输出：精简后的 progress.md
```markdown
# Project Progress

Last Updated: 2026-01-11 16:30

## 🔧 In Progress

- [ ] 用户权限管理 [In Progress] [Updated: 2026-01-10]
- [ ] 数据加密存储 [In Progress] [Updated: 2026-01-11]

## 📋 Pending Tasks

- [ ] 实现双因素认证
- [ ] 添加日志系统
- [ ] 性能优化
...（共 15 个活跃任务）

## ✅ Recently Completed

- [x] API 文档生成 [Completed: 2026-01-10]
- [x] 单元测试覆盖 [Completed: 2026-01-09]
```

### 执行过程
1. 检测到文件超过归档阈值
2. 创建备份文件
3. 分析任务，识别已完成和 30 天前的任务
4. 将 48 个任务迁移到 archive.md
5. 保留活跃任务和近期完成任务
6. 更新两个文件
7. 返回归档统计信息

---

## 示例5：优先级管理

### 背景
项目中有紧急任务和普通任务，需要清晰标记优先级。

### 对话内容
```
User: 生产环境发现严重 bug，需要紧急修复登录失败问题！
另外，还要优化一下首页加载速度，这个不那么急。

更新进度。

Claude: [识别到 Progress Recorder]
检测到优先级标记：
- "登录失败问题" → 紧急 🔥
- "首页加载速度优化" → 普通

✅ progress.md 已更新完成
```

### 输出：带优先级的 progress.md
```markdown
# Project Progress

Last Updated: 2026-01-11 17:00

## 🔥 High Priority

- [ ] 🔥 修复登录失败问题 [Updated: 2026-01-11]

## 🔧 In Progress

- [ ] 用户权限管理 [In Progress] [Updated: 2026-01-10]

## 📋 Pending Tasks

- [ ] 优化首页加载速度 [Updated: 2026-01-11]
- [ ] 实现双因素认证
```

### 执行过程
1. 解析对话，识别关键词 "紧急"、"严重"
2. 标记任务优先级为 urgent
3. 将紧急任务放在 "🔥 High Priority" 分类
4. 普通任务放在 "📋 Pending Tasks"
5. 更新文件

---

## 示例6：集成关键决策

### 背景
在技术选型和架构设计过程中做了重要决策，需要记录下来。

### 对话内容
```
User: 经过讨论，我们决定使用 Redis 做缓存，PostgreSQL 做主数据库。
认证方式选择 JWT 而不是 Session，因为要支持微服务架构。

记录这些决策和当前任务。

Claude: [识别到 Progress Recorder]
提取关键决策：
- 使用 Redis 做缓存
- PostgreSQL 做主数据库
- 选择 JWT 认证（原因：支持微服务）

✅ progress.md 已更新完成
```

### 输出：包含决策记录的 progress.md
```markdown
# Project Progress

Last Updated: 2026-01-11 18:00

## 🔧 In Progress

- [ ] 集成 Redis 缓存 [In Progress] [Updated: 2026-01-11]
- [ ] 实现 JWT 认证 [In Progress] [Updated: 2026-01-11]

## 📝 Key Decisions

- **2026-01-11**: 使用 Redis 做缓存，PostgreSQL 做主数据库
- **2026-01-11**: 选择 JWT 认证而非 Session，原因：支持微服务架构

## 💡 Notes

- 架构采用微服务模式
```

### 执行过程
1. 识别决策关键词："决定"、"选择"、"采用"
2. 提取决策内容和理由
3. 添加到 "📝 Key Decisions" 分类
4. 记录决策时间戳
5. 保持决策的完整上下文

---

## 示例7：手动触发归档（不满足自动阈值）

### 对话内容
```
User: python .claude/skills/progress_recorder/scripts/archive_progress.py progress.md --days 15

Output:
ℹ️  No archiving needed (thresholds not reached)
   Total lines: 320 / 500
   Completed tasks: 25 / 50
   File size: 55.3KB / 100KB
```

### 说明
如果不满足自动归档阈值，脚本会显示当前状态并退出，不执行归档操作。

---

## 示例8：验证和修复格式错误

### 背景
手动编辑 progress.md 导致格式混乱，需要验证和修复。

### 对话内容
```
User: 检查并修复 progress.md 的格式

Claude: [识别到 Progress Recorder]
正在验证 progress.md 格式...
发现问题：
- 第 15 行：缩进不一致
- 第 23 行：复选框格式错误 [x ] 应为 [x]
- 第 30 行：时间戳格式错误

正在修复...

✅ 格式已修复
```

### 执行过程
1. 读取并解析文件
2. 检测格式问题
3. 自动修复常见错误
4. 重新生成格式正确的文件
5. 报告修复内容

---

## 实用技巧

### 技巧1：快速查看统计信息
```bash
# 使用 grep 快速统计任务数量
grep -c "^\- \[ \]" progress.md   # 待办任务
grep -c "^\- \[x\]" progress.md   # 已完成任务
grep -c "🔥" progress.md          # 高优先级任务
```

### 技巧2：导出为其他格式
```bash
# 转换为纯文本待办列表
grep "^\- \[ \]" progress.md | sed 's/^- \[ \] //' > todo.txt
```

### 技巧3：与 Git 集成
```bash
# 设置 Git hook 自动提交进度
echo "git add progress.md && git commit -m 'chore: auto-update progress'" > .git/hooks/post-update
chmod +x .git/hooks/post-update
```

### 技巧4：定期归档（cron job）
```bash
# 每周日凌晨自动归档
0 0 * * 0 cd /path/to/project && python .claude/skills/progress_recorder/scripts/archive_progress.py progress.md
```
