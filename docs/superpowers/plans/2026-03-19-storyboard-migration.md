# Storyboard Migration Plan: Copilot → MediaHub

> 从 Storyboard-Copilot（Tauri 桌面应用）移植核心功能到 MediaHub（Web 应用）
> 日期: 2026-03-19
> 分支: feature/storyboard
> 源码: /Volumes/program/project-code/github-repos/Storyboard-Copilot/src

## 策略

**从头移植替换** — 删除当前脚手架代码，从 Copilot 源码移植，适配 Web + FastAPI 架构。

## 关键适配

| Copilot (Tauri) | MediaHub (Web) |
|-----------------|----------------|
| Tauri invoke() | FastAPI REST API |
| 本地文件系统 | NAS 存储 + API 提供文件 |
| MD5 去重 | SHA-256 去重 (storyboard_assets) |
| convertFileSrc() | 直接 HTTP URL |
| Tauri 设置存储 | Supabase + env |
| Zustand canvasStore | 复用，加 API 持久化 |

## 阶段 0: 准备框架

### Task 0.1: 清理当前脚手架
- 删除 `frontend/components/storyboard/` 全部内容
- 删除 `frontend/hooks/storyboard/`
- 删除 `frontend/stores/storyboardStore.ts`
- 保留：`frontend/services/storyboardService.ts`（API 层已验证可用）
- 保留：`frontend/pages/StoryboardWorkbench/`（路由框架）
- 保留：后端代码（API/Service/Repository/Tasks）

### Task 0.2: 创建新目录结构
```
frontend/features/storyboard/
├── application/          ← Copilot application/ 适配
│   ├── canvasServices.ts
│   ├── eventBus.ts
│   ├── imageData.ts      ← 重写
│   ├── nodeFactory.ts
│   ├── ports.ts
│   └── toolProcessor.ts  ← 重写
├── domain/               ← Copilot domain/ 直接复制
│   ├── canvasNodes.ts
│   ├── nodeDisplay.ts
│   └── nodeRegistry.ts
├── edges/                ← Copilot edges/ 直接复制
├── hooks/                ← Copilot hooks/ 直接复制
├── infrastructure/       ← 新建 HTTP 适配层
│   ├── httpAiGateway.ts
│   └── httpToolGateway.ts
├── models/               ← Copilot models/ 复制
├── nodes/                ← Copilot nodes/ 适配
├── tools/                ← Copilot tools/ 适配
├── ui/                   ← Copilot ui/ 复制+适配
├── Canvas.tsx            ← Copilot Canvas.tsx 适配
├── CanvasToolbar.tsx
└── NodeSelectionMenu.tsx
```

### Task 0.3: 复制零改动文件
从 Copilot 直接复制（不改动）:
- `domain/canvasNodes.ts` (241 行)
- `domain/nodeRegistry.ts` (290 行)
- `domain/nodeDisplay.ts` (61 行)
- `application/ports.ts` (104 行)
- `application/eventBus.ts` (40 行)
- `application/nodeFactory.ts` (30 行)
- `edges/` 全部
- `hooks/` 全部
- `pricing/` 类型定义

### Task 0.4: 创建 canvasStore
从 Copilot `stores/canvasStore.ts` (1,591 行) 复制，适配:
- 移除 Tauri 相关 import
- 保留所有 state 和 actions
- 添加 API sync 逻辑（调用现有 storyboardService）

## 阶段 1: 节点系统

### Task 1.1: UI 组件移植
从 Copilot `ui/` 复制并适配:
- NodeHeader.tsx — 浮动标题栏
- NodeResizeHandle.tsx — 调整手柄
- NodeActionToolbar.tsx — 操作工具栏
- CanvasNodeImage.tsx — 图片显示（移除 Tauri convertFileSrc）
- ModelParamsControls.tsx — 模型参数 UI
- NodeToolDialog.tsx — 工具对话框

### Task 1.2: 基础节点
- UploadNode.tsx — 适配 HTTP 上传（已有后端 API）
- ImageNode.tsx — 移除 Tauri 依赖
- TextAnnotationNode.tsx — 直接复制
- GroupNode.tsx — 直接复制

### Task 1.3: 画布集成
- Canvas.tsx — 替换 AiGateway 注入为 HTTP 版本
- CanvasToolbar.tsx — 直接复制
- NodeSelectionMenu.tsx — 直接复制
- 注册所有节点类型

## 阶段 2: 图片处理管道

### Task 2.1: imageData.ts 重写
- `resolveImageDisplayUrl()` → 直接返回 URL（无 Tauri 转换）
- `prepareNodeImageFromFile()` → HTTP 上传到后端
- `persistImageLocally()` → 调用上传 API
- `loadImageElement()` → fetch URL

### Task 2.2: toolProcessor.ts 重写
- `cropImageSource()` → 前端 Canvas 裁剪 或 后端 API
- `splitImageSource()` → 后端 `/split-image` API（已有）
- `annotateImage()` → 保留前端 Canvas 实现

### Task 2.3: HTTP Gateway
- `httpAiGateway.ts` — 实现 AiGateway 接口，调用 FastAPI
- `httpToolGateway.ts` — 实现 ImageSplitGateway 接口

## 阶段 3: 高级节点

### Task 3.1: ImageEditNode
- 复制 895 行，改 AI 调用为 HTTP
- 进度跟踪通过 Supabase Realtime（已有 unified_tasks 机制）

### Task 3.2: StoryboardNode
- 复制 1,361 行，改后端调用
- 导出合并图片 → 后端 PIL 处理

### Task 3.3: StoryboardGenNode
- 复制 1,699 行，改 AI 批量调用

## 阶段 4: 工具和集成

### Task 4.1: 工具编辑器
- CropToolEditor — 适配
- AnnotateToolEditor — 保留 Canvas 绘制
- SplitStoryboardToolEditor — 适配后端 API

### Task 4.2: 项目持久化
- projectStore 改为 Supabase 持久化
- 图片池编码 `__img_ref__:N` 保留
- 防抖同步保留

### Task 4.3: MediaHub 独有功能重新接入
- 角色系统（CharacterPanel）
- AI Chat 面板
- Timeline 播放器
- ImageToVideo 节点
- 侧边栏入口

## 阶段 5: 测试和优化

### Task 5.1: E2E 测试
- 上传图片 → 生成 → 拆分 → 导出 完整流程

### Task 5.2: 性能
- 图片懒加载
- 缩放级别切换图片质量
- 防抖优化
