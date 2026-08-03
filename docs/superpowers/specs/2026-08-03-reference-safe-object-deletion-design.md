# 引用安全的对象删除 设计文档

**Goal:** 让删除路径在"零 FS 全 S3"之后既**真的能删**(现在 sb:// 行删了个寂寞,对象全成孤儿),又**不会误删**(内容寻址导致多行共指同一对象,现有 media 删除会当场毁掉别的行的文件)。

## 现状(2026-08-03 生产库实测)

内容寻址 (`t{scope}/{sha[:2]}/{sha[2:4]}/{sha}{ext}`) + 全局下载缓存 ⇒ **同内容只存一份、多行共指**:

| 共用情况 | 行数 |
|---|---|
| 同 key 被 `resources.file_path` 与 `parsed_media.download_path` 同时引用 | **991** |
| 同 key 被 `resource_versions.file_path` 与 `resources.file_path` 同时引用 | **967** |
| 多个 `resources` 行共用同一 key | 2 组(一组 4 行) |

由此两类缺陷:

1. **静默孤儿(删不掉)** —— `resources_service._delete_physical_files`(:911,4 处调用)与 `delete_version`(:517) 都是 `Path(DOWNLOAD_PATH) / file_path`;file_path 现在是 `sb://...`,拼出的路径不存在 → 一个字节都没删,S3 对象永久孤儿。用户删除 / 回收站 GC **不再释放任何空间**,直接抵消存储回收成果。
2. **误删(删太多)** —— `media_router._delete_stored_file`(:194)已 S3-aware 但**无条件 remove**。删一个 parsed_media 会移除 991 组共享对象中对应的那个,而 `resources.file_path` 仍指着它 → 资源库里那份**当场变 404**。这是今天就能触发的数据丢失,不是理论风险。

## 设计:一个引用感知的删除原语

新增 `app/services/library/object_gc.py`(独立小模块,不塞进已经很大的 media_storage):

```python
async def delete_object_if_unreferenced(
    raw_path: str, *, exclude: dict[str, list] | None = None
) -> str: ...
# 返回 "deleted" | "kept_referenced" | "skipped_fs" | "noop"
```

规则:

- **前缀形态**(`loc.is_prefix`:album `t{s}/album/{rid}/`、HLS `hls/{rid}/{vid}/`、derived `derived/{rid}/`)—— 这些 key **按 rid/vid 命名,天然独占**,不共享 ⇒ 直接 `remove_prefix`,不做引用检查。
- **单对象形态**(内容寻址)—— **先查引用**:除了正在删的那一行,还有没有别的活行指向同一个 `raw_path`?有就跳过(记 `kept_referenced`),没有才 `remove`。
- **非 sb://** —— 交回调用方的文件系统逻辑(本模块不碰)。

引用检查覆盖全部 9 个索引列(与 `storage_audit._COLLECT_SQL` 同一份清单,注释里互相指认以防漂移):
`parsed_media.{download_path,cover_download_path,music_download_path,extract_audio_path}`、
`resources.{file_path,cover_image_path,thumbnail_path}`、
`resource_versions.{file_path,hls_path}`

`exclude` 让调用方声明"这些行正在被删,不算引用"(如 `{"resources":[rid]}` / `{"resource_versions":[vid]}` / `{"parsed_media":[pmid]}`)。**调用顺序要求:先删 DB 行、再调本函数**,这样引用查询天然看不到已删的行,`exclude` 只作为"DB 行还没删就先清对象"场景的兜底。

失败语义沿用 `media_router` 既有取向:删除失败只 warning,不让删除端点 500(对象泄漏比"删不掉记录"轻,且可被后续 GC 收拾)。

## 接线点

| 位置 | 现状 | 改为 |
|---|---|---|
| `resources_service._delete_physical_files` | 纯 FS,sb:// 无效 | 先走 `delete_object_if_unreferenced`(file_path / cover_image_path / thumbnail_path + `derived/{rid}/` 前缀),非 sb:// 才走原 FS 逻辑(逐行保留) |
| `resources_service.delete_version` | 纯 FS | 同上(版本的 file_path + hls_path 前缀) |
| `media_router._delete_stored_file` | 无条件 remove(**会误删**) | 单对象改走引用检查;前缀分支保持 `remove_prefix` 不变 |

## 明确不做(YAGNI)

- 不建引用计数表/不加 DB 约束(查询即真相,行数级几千,一次 UNION 查询毫秒级)
- 不做全库孤儿 GC 扫描(已有 admin Deep Verify 能发现"DB 指向但对象不存在";反向的"对象存在但无人指向"留待有需要时再做)
- 不动 `generated_media_repository` / `attachment_service` / `hls_publisher.clear`(各自 key 独占,已正确)

## 测试

- 前缀 → `remove_prefix` 被调、不做引用查询
- 单对象**有**其它引用 → 不删,返回 `kept_referenced`(用 991 组那种 pm↔resources 共指构造)
- 单对象**无**其它引用 → `remove` 被调
- `exclude` 生效:自己那行不算引用
- 非 sb:// → `skipped_fs`,不碰对象存储
- 存储调用失败 → 不上抛(warning),返回值反映失败
- `_delete_physical_files` / `delete_version` 对 sb:// 行确实调用了新原语;对 legacy fs 行逐行保持原行为
- `media_router` 删 media 时,若 `resources` 仍引用同一 key → 对象**不被删**(回归 991 组的误删 bug)

## 验收(部署后)

构造一对共指行(或用现有 991 组之一的只读探测),确认:删 media 后 resources 那份仍可服务;删 resource 后对象确实从 S3 消失(无其它引用时)。
