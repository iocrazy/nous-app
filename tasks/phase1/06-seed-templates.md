# Task 06: 预设模板 + 内置能力 Seed 数据

## 目标

创建 3 个预设工作流模板和内置能力模块的种子数据。

## 依赖

- Task 01（数据库表已创建）

## 输出文件

```
supabase/migrations/085_seed_workflow_templates.sql
```

## 内置能力模块（capabilities）

```sql
INSERT INTO capabilities (id, name, slug, type, description, icon, action_type, is_builtin, is_active) VALUES
(1, 'Topic Analysis',    'topic-analysis',  'ai_agent', 'Analyze trending topics and generate content ideas', '🔍', 'ai_topic_analysis', true, true),
(2, 'Copywriting',       'copywriting',     'ai_agent', 'Generate titles, descriptions, and hashtags', '✍️', 'ai_copywriting', true, true),
(3, 'Media Collection',  'media-collect',   'internal', 'Parse and download reference media via MediaHub', '📦', NULL, true, true),
(4, 'Cover Generation',  'cover-gen',       'ai_agent', 'AI-generate cover images for content', '🎨', 'ai_cover_gen', true, true),
(5, 'Compliance Check',  'compliance',      'ai_agent', 'Check content for sensitive words and platform rules', '🛡️', 'ai_compliance', true, true),
(6, 'Multi-Platform Publish', 'multi-publish', 'api',   'Publish content to Douyin, Xiaohongshu, Bilibili', '📤', NULL, true, true),
(7, 'Data Collection',   'data-collect',    'api',      'Collect performance data from platforms', '📊', NULL, true, true),
(8, 'Data Analysis',     'data-analysis',   'ai_agent', 'Generate performance analysis reports', '📈', 'ai_data_analysis', true, true),
(9, 'Script Generation', 'script-gen',      'ai_agent', 'Generate video scripts and storyboards', '📝', 'ai_script_gen', true, true);
```

## 模板一：抖音/短视频内容生产（10 节点）

节点定义：

| 序号 | 名称 | 类型 | 分组 | 角色 | 能力 | auto_advance |
|------|------|------|------|------|------|-------------|
| 1 | Topic Planning | ai_agent | preparing | Director | topic-analysis | false |
| 2 | Script Writing | ai_agent | preparing | Director | script-gen | false |
| 3 | Media Collection | ai_agent | preparing | Director | media-collect | true |
| 4 | Shooting | human | in_progress | Cameraman | - | false |
| 5 | Editing | human | in_progress | Editor | - | false |
| 6 | Cover Design | ai_agent | in_progress | Designer | cover-gen | false |
| 7 | Copy & Title | ai_agent | in_progress | Operator | copywriting | false |
| 8 | Review | approval | reviewing | Manager | compliance | false |
| 9 | Publish | api | reviewing | Operator | multi-publish | true |
| 10 | Data Review | ai_agent | completed | Operator | data-analysis | true |

连线：1→2→3→4→5→6→7→8→9→10（线性）
审核节点有条件分支：通过→9, 驳回→5（回退到剪辑）

## 模板二：视频审片（6 节点）

| 序号 | 名称 | 类型 | 分组 | 角色 |
|------|------|------|------|------|
| 1 | Upload | human | preparing | Editor |
| 2 | First Review | approval | reviewing | Reviewer |
| 3 | Revision | human | in_progress | Editor |
| 4 | Second Review | approval | reviewing | Reviewer |
| 5 | Final Review | approval | reviewing | Manager |
| 6 | Archive | api | completed | System |

连线：1→2→(通过)3→4→(通过)5→6, 2→(驳回)3, 4→(驳回)3

## 模板三：图文内容（6 节点）

| 序号 | 名称 | 类型 | 分组 | 角色 | 能力 |
|------|------|------|------|------|------|
| 1 | Topic | ai_agent | preparing | Director | topic-analysis |
| 2 | Writing | ai_agent | in_progress | Writer | copywriting |
| 3 | Illustration | ai_agent | in_progress | Designer | cover-gen |
| 4 | Layout | human | in_progress | Designer | - |
| 5 | Review | approval | reviewing | Manager | compliance |
| 6 | Publish | api | completed | Operator | multi-publish |

连线：1→2→3→4→5→6

## 积分定价（point_pricing 表已存在）

为新的 AI 能力添加定价：

```sql
INSERT INTO point_pricing (action_type, points_cost, description, is_active) VALUES
('ai_topic_analysis', 5, 'AI topic analysis and trending content research', true),
('ai_copywriting', 3, 'AI-generated titles, descriptions, and hashtags', true),
('ai_cover_gen', 8, 'AI cover image generation', true),
('ai_compliance', 2, 'AI content compliance check', true),
('ai_data_analysis', 5, 'AI performance data analysis report', true),
('ai_script_gen', 5, 'AI video script and storyboard generation', true);
```

## 验收标准

- [ ] 9 个内置能力模块已创建
- [ ] 3 个预设模板已创建（含 nodes + edges）
- [ ] 模板的 is_template = true
- [ ] AI 能力的 action_type 与 point_pricing 匹配
- [ ] 审片模板的条件分支正确（通过/驳回两条 edge）
- [ ] 在前端模板选择器中可以看到 3 个模板
