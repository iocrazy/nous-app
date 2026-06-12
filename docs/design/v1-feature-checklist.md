# v1 精修 — 资源库页功能保真验收清单（D12 基线）

> 2026-06-13 由代码盘点生成（~260 交互点）。v1 纯视觉改版前的功能基线；
> 完工后逐项核对，任何一项行为变化 = 违反 D12。

## 1. ResourcesSidebar（components/ResourcesSidebar.tsx）
- [ ] 分享管理 → /resources/shared（L133-138）
- [ ] 回收站 → /resources/recycle（L140-146）
- [ ] 侧栏折叠/展开按钮（collapsed 状态，L113-121, 359-368）
- [ ] 〔个人〕我的下载 → /resources/downloads + 计数徽章（L240-249）
- [ ] 〔个人〕我的资源（根）→ /resources + 计数 + **拖放目标**（onSidebarDragOver/Drop）+ 新建文件夹 ＋ 按钮（L253-274）
- [ ] 〔个人〕临时 → /resources/temp（L278-284）
- [ ] 〔团队〕Library 父项展开/折叠 + 计数 + 新建 ＋（L157-176）
- [ ] 〔团队〕Library 子项导航 /resources/library/{id}；内联创建输入（Enter 确认/Esc·blur 取消/saving 旋转）（L180-235）
- [ ] Smart Folders 父项展开/折叠 + 计数 + 新建 ＋（L295-314）
- [ ] Smart Folder 子项导航 /resources/smart/{id}；hover 铅笔编辑；**右键菜单**（编辑/删除）；空态占位按钮（L318-356）

## 2. ResourceGrid 工具栏（components/ResourceGrid.tsx）
- [ ] ToolbarSearch：三模式（Quick/Smart/AI）切换+icon 变色；keyword 实时过滤；AI 模式 Enter 触发；Esc 关闭；清除 X；搜索中 Loader；Scope 下拉（至少保留一个）
- [ ] 过滤漏斗切换 FilterBar 显隐 + localStorage 持久化（L545-568）
- [ ] 排序下拉（6 项，选中 Check，非默认时按钮变色）（L571-606）
- [ ] 视图切换循环 grid→justified→list（L608-631）
- [ ] 上传按钮（进度百分比/上传中禁用）+ 下拉（File/Folder）（L637-680）
- [ ] 新建下拉：文件夹/Project(soon)/Smart Folder/文档(soon)/表格(soon)/演示(soon)/网页 URL→FetchUrlModal（L683-755）
- [ ] 面包屑分段导航（L519-525）
- [ ] 多选 toolbar：Select All / Cancel / 计数（L775-796）
- [ ] FilterBar 各 chip：Tags/Rating/Type/Source/AI Status/Date/Duration/Aspect/Social + Clear All + Filter Config（pin/reorder）
- [ ] 拖放上传覆盖层；回收站清理提示条；分享 Coming Soon 占位；移动端回收站快捷链接
- [ ] 新建文件夹内联输入（autoFocus/Enter/Esc/blur 空取消/saving）（L867-891）
- [ ] 无限滚动哨兵（IntersectionObserver 600px，searchQuery 为空时才触发）（L368-388）

## 3. ResourceCard（components/ResourceCard.tsx）
- [ ] 单击延迟 250ms 开信息面板（再点同项关闭）；双击 → /resources/file/{id}；右键菜单（L177-231）
- [ ] 多选 checkbox：单选/Shift 范围/Cmd toggle；hover 显示或 forceShow（L261-273, 354-376）
- [ ] 拖动：单项/多项（计数徽章）；MIME application/mediahub-items（L225-259）
- [ ] 右键菜单项：View Details/Open in New Tab/Download Original(成败 toast)/Rename/Upload New Version/Copy To/Move To/Share/Move to Trash
- [ ] 缩略图 lazy + 失败 fallback icon；**视频 hover scrub**（sprite 预载/mousemove 帧/动态时间戳/底部进度条）；时长徽章（L131-221, 423-440)
- [ ] hover 三点菜单按钮；删除（非回收站）/恢复+永久删除（回收站）hover 钮（L277-285, 445-471）
- [ ] 文件名双击重命名（autoFocus/Enter/Esc/blur）；Transcoding 徽章（L475-499）
- [ ] List 模式：类型 icon/大小/日期/回收站 TTL 倒计时（≤3 红 ≤7 琥珀）/hover actions（L303-373）

## 4. FolderCard（components/FolderCard.tsx）
- [ ] 单击开面板/双击进入 /resources/folder/{id}/右键菜单（Get Info/Open in New Tab/Open/Rename/Copy To/Move To/Share/Trash 含内容确认）
- [ ] **拖放目标**：dragOver ring（indigo）/dragLeave/drop → onDropItems（L98-124）
- [ ] checkbox 同 ResourceCard；⋮ 菜单按钮
- [ ] Grid 模式：**tab 耳朵（38% 宽，hover 变色）**（L224-226）；2×2 预览缩略图（fallback icon/空文件夹琥珀 icon hover 放大）；子项计数徽章；名称双击重命名；日期
- [ ] List 模式：琥珀 FolderIcon/名称双击重命名/N items/日期/hover 菜单

## 5. ResourceInfoPanel（components/ResourceInfoPanel.tsx）
- [ ] 粘性标题 + 关闭 X（L232-239）
- [ ] 缩略图（失败 fallback、底色按类型）（L242-255）
- [ ] 文件名 click-to-edit（hover 铅笔/Enter·blur 提交/Esc 取消）；readOnly 纯显示（L258-282）
- [ ] 备注 textarea（blur 自动保存 debounce）；readOnly 有值才显示（L286-308）
- [ ] URL 输入（blur 保存/Enter 触发 blur）；readOnly 可点链接 target=_blank（L311-327）
- [ ] EagleTagPicker：显示/添加/移除/新建标签（L330-337）
- [ ] 文件夹归属显示（L339-350）
- [ ] AI Status 三行 badge（Transcript/Summary/Visual，processing/completed/failed/pending）（L352-390）
- [ ] Properties：星级（点击 1-5/再点清除/hover 预览/amber fill）/时长 MM:SS/大小/类型/分辨率 WxH/版本 v{n}(>1)/来源 Web|Upload/创建/修改时间（L393-417）

## 6. ResourcesInfoPanelWrapper
- [ ] 固定右板 showInfoPanel 平移显隐；宽度 240-600px
- [ ] 左缘折叠把手（开时）；视口右缘展开把手（关时）（L98-151）
- [ ] **左缘拖拽调宽**（col-resize 游标/禁 user-select/240-600 钳制）（L57-110）
- [ ] 文件夹选中 → FolderInfoPanel；资源 → ResourceInfoPanel；回收站 readOnly

## 7. BatchSelectionToolbar
- [ ] 底部居中浮条，selectedIds>0 且非 Downloads 显示
- [ ] 计数（resources.selected）
- [ ] 回收站：批量恢复（资源+文件夹，刷新+清选）/批量永久删除（确认模态）
- [ ] 正常：批量移动（资源+文件夹→picker move）/批量复制（仅资源→picker copy）/批量删除（API+刷新+清选）
- [ ] X 清空选择

## 8. 跨组件
- [ ] 键盘：Delete·Backspace 删 / R 重命名 / Enter 打开 / Cmd+A 全选 / Esc 清选 / Cmd+C·X·V 复制剪切粘贴
- [ ] 空白区右键：Upload File/New Folder/Refresh/Paste(有剪贴板时)
- [ ] 视图态：Downloads 隐侧栏；Recycle 显恢复+TTL；Temp 显 TTL 禁拖传；Shared 占位
- [ ] 权限：canUpload/download/update/delete 控制对应入口显隐；readOnly 禁编辑
- [ ] 各计数徽章（侧栏×4 + 文件夹卡）

> 完整 260 项原始盘点见 git history；本文件为按 v1 触碰面归并的核对版。
