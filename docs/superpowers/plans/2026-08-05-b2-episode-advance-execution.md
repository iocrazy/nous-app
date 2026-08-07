# B2「推进机器改造」实施计划(Plan 子 agent 产出,2026-08-05)

## 0. 前置阻塞:worktree 落后 origin/master 31 commit(A线+B1代码在master不在本worktree)
第一步必须 `bash scripts/sync-worktree.sh` rebase。否则 migration 402 缺失、`EpisodeRepository.set_current_node_id` 缺失。

## 1. 范围边界
**属于B2**:advance_service(631行)project→episode 作用域;9函数加episode_id;写路径改调 EpisodeRepository.set_current_node_id;P0陷阱①②必破;autopilot tick按P0§3.3;advance API入口加episode_id;前端10处+14测试跟改。
**留给B3-B6**:陷阱③instantiate_from_template(B3);完成判据(B4);_deliverable_present fallback fail-closed(B4);UI重构(B5);Canvas退役/遗留清理(B6);数据迁移(B3/B6)。
判据:B2=推进机器认识episode;B3=节点按episode生产;B4=完成态按surface算;B5=UI按集重排。

## 2. P0三陷阱
**①parallel_group/sort_order跨集碰撞(B2第一件)**:instantiate_from_template(project_stage_nodes_repository.py:201)拷模板原值→每集同值;_build_groups(:50)/_active_index(:75)吃list_nodes(project_id)全项目节点;get_active_group(:862)SQL不带episode过滤。破法:新增list_nodes_by_episode,compute_advance_preview/execute_advance改用它;get_active_group加episode_id过滤;_active_index签名不变但调用方保证喂单集。不破→推进一集=推进所有集,_active_index静默return 0从头重推,单集测不出。
**②多集共用交付物文件夹(B2阻塞,不可拖B4)**:ensure_node_folder(node_folders.py:29-85)按名字大小写不敏感复用项目根同名文件夹→Ep2/Ep3命中Ep1的"Script"文件夹同folder_id;_deliverable_present主路径(advance_service.py:158-168)list_folder_files非空即过→Ep1交一个文件Ep2/Ep3 gate2放行。破法:文件夹名加集前缀(如"Ep2 · Script")。不破→按集推进第一次真实使用就连跳三集。
**③instantiate_from_template吞第2集(不在B2→B3)**:幂等"项目已有节点就不动"+advisory lock按project_id。B2只预留episode_id参数透传,不改实例化逻辑。

## 3. advance_service迁移清单
24点(后端14/前端10)在master复核仍准。后端写点4个:set_current_node_id(project_stage_nodes_repository.py:935,B2不改此函数,而是advance_service 3调用方改调EpisodeRepository.set_current_node_id即episode_repository.py:306);instantiation.py:87(B3);advance_service.py:535前进/:580后退改episode版。读点:advance_service :357/:520。
九函数机械vs语义:_build_groups(50)语义最大坑必须吃单集;_active_index(75)机械纯函数;_deliverable_present(137)语义受陷阱②;_unmet_dependency_names(239)语义单集map;compute_advance_preview(333)机械+语义,resolve_effective_role保持项目级;_preview_forward(373)语义Gate4变"本集做完";_preview_back(477)机械;execute_advance(495)语义6副作用点跟改;_enqueue_autopilot_tick(611)机械建议不改。
六闸门:Gate0角色不下沉;Gate1/3/5判据不变;Gate2受陷阱②;Gate4语义变。原样可用6个:_node_ref(91)/_mirror_issues(105)/_first_issue_identifier(120)/_review_satisfied(131)/_form_incomplete(203)/_open_subissue_warnings(310)。

## 4. 任务分解(SDD)
前置:sync-worktree rebase。
- **T0 数据入口按集过滤(仓库层)** 无依赖先做。project_stage_nodes_repository.py:list_nodes_by_episode + get_active_group加episode过滤。验收:2集项目断言只返回本集节点。
- **T1 advance_service作用域下沉** 依赖T0。9函数;compute_advance_preview(project_id,episode_id,user_id,direction);execute_advance加episode_id;_build_groups吃单集;3写点改episode版。验收:推进ep1只动ep1游标;test_advance_predicate.py fake改_FakeEpisodesRepo。
- **T2 交付物文件夹按集命名(破②)** 可与T1并行。node_folders.py文件夹名带集前缀。验收:ep1/ep2 Script不同folder_id;ep1交文件后ep2 gate2仍BLOCK_DELIVERABLE_MISSING。
- **T3 advance API入口加episode_id** 依赖T1。projects_router.py(/advance:869 /advance-preview:856 GET/workflow:364 start-early:767)。
- **T4 autopilot计量按P0§3.3** 依赖T1。autopilot.py:tick保持每项目一个(autopilot_tick(project_id)不变:456);_cascade_pass/_auto_start_pass内循环按集execute_advance;每集子上限per_episode_cap=max(3,ceil(daily/活跃集数));_auto_start_pass排序(episode.sort_order,node.sort_order)。关键:tick不下沉否则used计数并发失效硬顶破2×;autopilot_sweep.py不改。
- **T5 前端10处+测试** 依赖T3。WorkspaceTopBar:92/WorkspaceStageBoard:173/WorkflowSection:244,261/WorkflowStrip/nodeStatus:62/issueFlow:151/WorkspaceSidebar/ProjectWorkspace:478/types:1256。侧栏阶段列表删除留B5。4套e2e。
并行:T0先;T1+T2并行;T3+T4依赖T1后并行;T5依赖T3。

## 5. 迁移编号
B2大概率不需新migration(B1 mig402已落所需列;陷阱①②是代码层)。若需要:master最大406,本会话开了407(PR#1709未合并),建议取408,取号前再git ls-tree复核。

## 6. 风险
A线AgentRunScope交互:autopilot按集派发的agent run须烙对episode_id(与推进集一致);resolve_effective_role保持项目级不下沉。task_tracking纪律:execute_advance副作用enqueue_stage_hook_dispatch若触DBOS走manager API禁PATCH phase列;暂停文案属metadata合规。DBOS:autopilot_tick保持每项目一个不碰cascade_in_progress重入守卫;不在@DBOS.step内启workflow;新字段写metadata不写workflow input。测试陷阱:生产仅「个人项目测试1」291022264100262有4集,B2验收必须造≥2集项目否则假绿。

关键文件:advance_service.py / project_stage_nodes_repository.py / node_folders.py / autopilot.py / projects_router.py;写路径落点 episode_repository.py:set_current_node_id(仅master有,rebase后可用)。
