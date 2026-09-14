# 产出登记（run_deliverables）的两条常被问错的口径

## C5 — undo 不占版本号
`services/ai/undo/run_undo_service.py` 的回滚是**逆操作批**：它按 `run_id`
读 `script_shot_ops` / `script_ops` 并写回逆操作，**不调 `register_deliverable`**，
所以血缘链上不会多出一版。理由：undo 撤销的是某个 run 干过的事，语义是
「那次产出不算数了」，再登记一版等于把撤销本身记成一次新产出。
与 3b 的 **revert** 正相反——revert 是人手发起的新版本（`actor_user_id` 占号、
`reverted_from_version` 指回目标版），因为它产生的是一个**新的当前状态**。
两者共用账本，不共用版本号。

## C7 — `generated_media` 恒 v1
媒体类没有版本链：重新生成产生的是**新的 `ref_id`**（新 `generated_media`
行），两代之间只共享 `node_id`。所以 `GET /outputs/generated_media/{id}` 永远
只回一版，`revert` 对它是 400 `kind_not_revertible`，来源块的 Diff 按钮
（`versions >= 2` 才渲染）在媒体上永不出现。要把两代媒体串成一条链需要
`shot_id + slot` 或 `parent_resource_id` 作键——不在 3b 范围（spec §6）。
