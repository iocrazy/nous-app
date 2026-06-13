# Project Assets（工程资产）— IC-port P4 设计

日期：2026-06-13 ｜ 状态：已批准 ｜ 来源：Infinite-Canvas「画布资产」tab 搬运（P4，最后一块）

## 背景与目标

IC 原版在素材管理器里有一个「画布资产」tab：扫所有画布 JSON 的 nodes 抠出素材引用，建反向索引，按画布分组浏览素材。MediaHub 已具备同构地基且更干净：

- `canvases` 表（mig 280）：project 域、`nodes_json` JSONB、kind smart/classic
- 画布节点用 **resource_id** 引用素材（非裸 URL）：`ShotNodeData.reference_resource_ids[]`（参考图）、`OutputNodeData.resource_id`（AI 产出）
- 素材网格、右键 AI 操作、批量打标/导出（P1-P3）全部现成

目标：资源库的 **Temp tab 重定位为 Project Assets**——按 Project → Canvas 虚拟树分组浏览画布引用的素材；Resource 详情页加「出现在哪些画布」反查。

## 已拍板的决策

1. **视图位置**：① 资源库 Temp tab 改名重定位为 Project Assets（主视图）；② Resource 详情页加反查块。
2. **Temp 区去留**：重定位（非并列、非彻底合并）。原 chat 临时上传作为树根下的 **Chat Uploads** 分组保留。
3. **TTL 取消**：`temp_resource_sweeper` 调度停用，工程文件删除由用户自己决定。Settings 的 `chat_temp_ttl_days` 行隐藏。
4. **数据层**：物化关联表（方案 A），不抄 IC 的实时全扫（那是单机 JSON 文件的产物，100k 设计目标下不可接受）。

## 设计

### 1. 数据层

新迁移（编号取当时最新 +1）：

```sql
CREATE TABLE canvas_resource_refs (
  canvas_id   BIGINT NOT NULL REFERENCES canvases(id) ON DELETE CASCADE,
  resource_id BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  role        TEXT   NOT NULL CHECK (role IN ('reference', 'output')),
  node_id     TEXT   NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (canvas_id, resource_id, node_id)
);
CREATE INDEX idx_crr_resource ON canvas_resource_refs(resource_id);
CREATE INDEX idx_crr_canvas   ON canvas_resource_refs(canvas_id);
```

维护：`CanvasService.save`（PUT 路径）从 `nodes_json` 提取引用集合 → 与现有 refs diff → 增删。幂等；失败记日志**不阻塞画布保存**（refs 可随时由 backfill 重建）。

Backfill：一次性脚本扫现有 `canvases.nodes_json`（量小），可重复执行。

TTL 退役：`temp_resource_sweeper` 的 `@DBOS.scheduled` 调度关闭（代码保留 + 注释说明 kill-switch 原因与重启方式）；Settings UI 隐藏 `chat_temp_ttl_days` 行（后端设置项保留不删）。

### 2. API（均挂 /api/v1）

| 端点 | 说明 | 权限 |
|------|------|------|
| `GET /resources/project-assets/tree` | 当前用户可见 projects → canvases 树，节点带 asset_count | 按 project membership 过滤 |
| `GET /canvases/{id}/assets` | 单画布素材列表（refs JOIN resources，带 role） | 复用 `_gate_canvas_read` |
| `GET /resources/{id}/canvas-refs` | 反查：素材出现在哪些画布（id/name/project/role） | 资源归属校验（复用现有 access check） |

### 3. 前端

- 资源库 Temp tab → **Project Assets**：
  - 左侧虚拟树：Project → Canvas（非真 folders 行），canvas 节点带素材数与 kind 标识；根下保留 **Chat Uploads** 分组（原 temp folder 内容，不再过期）。Chat Uploads 的数据**不走** tree 端点——沿用现有 temp folder 查询路径（`tempResources`），仅展示层归到树下
  - 右侧复用现成 `ResourceGrid`：缩略图/多选/右键 AI 操作/批量打标/Training Set 导出全部直接可用
  - 素材卡片带 reference/output 角色徽标；「Open in Canvas」跳转画布
- `ResourceDetailPage` 加 "Appears in N canvases" 块 + 跳转链接
- 路由：`/resources/temp` → `/resources/project-assets`（旧路径 redirect）
- i18n：en/zh 文案，UI 英文

### 4. 测试

- refs 提取/diff 单测：加节点、删节点、改引用、重复引用去重
- 三个端点的权限测试（非成员 403 / 跨用户资源 404）
- backfill 幂等测试
- 前端 tab/树渲染基本用例

### 5. 不做（YAGNI）

- 素材拖入画布的新交互（画布编辑器已有自己的添加方式）
- 跨 project 移动素材
- smart/classic 单独筛选维度（树节点带 kind 标即可）
- chat_upload 落盘路径改动（Chat Uploads 分组只是展示层归组）

## 风险与边界

- **refs 与 nodes_json 漂移**：save 路径失败时 refs 可能滞后 → backfill 可重建；反查/树读到的是物化值，可接受最终一致。
- **TTL 取消后 temp 积压**：用户自担；Chat Uploads 分组让这些文件可见可管理，比静默过期更可控。
- **canvas run output 持久化尚未全接通**（Phase 4 遗留）：P4 只读 `nodes_json` 已有的 resource_id，output 管线接通后自动出现在视图里，无耦合。
