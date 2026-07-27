# Provider Protocol Registry — 协议显性管理设计

日期:2026-07-13
状态:设计已获用户批准(会话内)
背景事故:#1313 / #1314 / #1319(chat 全挂一周:actual_provider 自由文本 + 前缀猜测分发)

## 问题

`mediahub_models.actual_provider` 在 admin 端是必填自由文本(`admin/src/pages/ai/index.tsx:680`),
但后端实际存在两套隐式"协议注册表":

- **chat 家族**(llm/embedding/asr):`app/services/ai/adapters/factory.py` 的
  `_PROVIDER_KEYS = {claude, openai, deepseek, doubao, modelscope, qwen}` +
  `resolve_provider_key` 三级阶梯(显式 key → 前缀规则 → OpenAI 兼容兜底)。
- **image/video**:`app/services/media/parsers/video_providers/db_registry.py` 的
  `_ARK_PROVIDERS = {doubao, ark}` / `_JIMENG_PROVIDERS = {jimeng-cli, jimeng}`。

admin 盲填 + 双份代码内清单 = 前后端语义脱节的温床(参照 IC 的协议下拉,
IC 把协议当一等公民管理:OpenAI 直连/异步协议/Gemini/方舟 Ark/RunningHub/即梦 CLI 等)。

## 目标(本期范围:显性化现有协议,不新增协议实现)

1. 后端一份**协议注册表**作为唯一事实源,现有两套代码内清单从它派生。
2. admin 端 Actual Provider 改为**按 type 过滤的下拉**,带说明,允许自定义值。
3. 零 DB 迁移、零数据改动、不加拒绝性校验(dispatch 已安全降级,保持 fail-open)。

明确不做(YAGNI / 后续 epic):移植 IC 的 Gemini / RunningHub / 异步协议 / Codex CLI /
Antigravity CLI 等新协议实现。

## 设计

### 1. 注册表模块 `backend/app/services/ai/provider_protocols.py`

纯数据 + 少量查询函数,无 IO:

```python
@dataclass(frozen=True)
class ProviderProtocol:
    key: str            # 规范 key,dispatch 用
    label: str          # admin 显示名
    aliases: tuple[str, ...] = ()   # 等价写法(如 jimeng → jimeng-cli)
    model_types: tuple[str, ...]    # 适用的 mediahub_models.type
    description: str    # admin 下拉副文本
    is_default: bool = False        # 未知标签的兜底协议(每 type 家族至多一个)
```

内容(与现网代码/数据一致):

| key | label | aliases | model_types | is_default |
|---|---|---|---|---|
| qwen | OpenAI-Compatible (generic) | — | llm, embedding, asr | ✅ |
| openai | OpenAI (native) | — | llm, embedding, asr | |
| claude | Claude (Anthropic) | — | llm | |
| deepseek | DeepSeek | — | llm | |
| doubao | Doubao / Volc Ark (chat) | — | llm, embedding, asr | |
| modelscope | ModelScope | — | llm | |
| ark | Ark task (方舟) | doubao | image, video | |
| jimeng-cli | Jimeng CLI (即梦) | jimeng | image, video | |

查询函数:`chat_provider_keys() -> frozenset[str]`、
`generation_keys_for(protocol_key) -> frozenset[str]`(key+aliases)、
`protocols_for_type(model_type) -> list[ProviderProtocol]`、`all_protocols()`。

### 2. 派生改造(消灭双份清单)

- `factory._PROVIDER_KEYS` ← `chat_provider_keys()`。
- `db_registry._ARK_PROVIDERS` / `_JIMENG_PROVIDERS` ← `generation_keys_for("ark"/"jimeng-cli")`。
- 行为零变化(集合内容 byte-identical),纯来源收敛。

### 3. API

`GET /api/v1/admin/mediahub-models/protocols`(挂在现有
`app/api/admin/nous_model_router.py`,admin-gated,只读):

```json
{ "protocols": [ {"key", "label", "aliases", "model_types", "description", "is_default"} ] }
```

### 4. Admin UI(`admin/src/pages/ai/index.tsx`)

- 页面加载时拉一次 protocols(失败降级回文本框,不阻塞页面)。
- Actual Provider FormItem:文本框 → Arco `Select`,选项 =
  `protocols.filter(p => p.model_types.includes(row.type))`,每项主文本 label、
  副文本 description,`allowCreate` 允许任意自定义值。
- 存量值不在列表(如未来用户填 nous)照常回显为自定义项。

### 5. 兼容与安全

- 无 DB 迁移;POST/PATCH 不新增校验(未知标签走兜底协议,行为已由
  #1313/#1319 测试钉死)。
- health probe / 分发逻辑不变。

### 6. 测试

- **契约测试**(核心,防再漂移):注册表 chat keys == factory `_PROVIDER_KEYS`;
  ark/jimeng 的 key+aliases == db_registry 两个集合;每个 (type 家族) 的
  is_default 至多一个且 chat 家族的 default == resolve_provider_key 的兜底("qwen")。
- protocols 端点:直接调函数单测(admin 端点惯例,不走 TestClient)。
- admin:`npm run build` 通过;下拉渲染逻辑保持轻量不加前端单测。

## 验收

- admin Models 弹窗按 type 显示协议下拉且可自定义输入;
- `grep -rn "_PROVIDER_KEYS = frozenset" factory.py` 不再有字面集合;
- 契约测试红线:任何人往 factory/db_registry 加协议而不更新注册表 → CI 红。

---

## Phase 2 — 协议策略模式类化(2026-07-13 追加,用户批准)

Phase 1 让协议**元数据**收敛到单一注册表;Phase 2 让协议**行为**(adapter/provider 构造)也收敛——每协议一个自包含模块,自己拥有 `build_*` 方法,`factory` 和 `db_registry` 的两条 if/elif 分发链收进各协议类。加新协议 = 加一个 `<key>.py`,分发层零改动。

### 目录:`provider_protocols.py` → 包 `provider_protocols/`

`__init__.py` 重导出全部 Phase 1 公共 API(`all_protocols`/`chat_provider_keys`/`generation_keys_for`/`default_chat_key`/`ProviderProtocol`)+ 新增 `get_chat_protocol` / `resolve_generation_protocol` + `ProviderNotConfiguredError`(从 factory 迁来),所有现有 `from app.services.ai.provider_protocols import ...` 保持可用。

- `base.py` — `ProviderProtocol` 基类(元数据 class-attr + 3 个 build 钩子,默认抛 `ProtocolCapabilityError`)+ `ProviderNotConfiguredError`(迁自 factory)。
- `_registry.py` — 组装 `PROTOCOLS` 实例元组 + 查询函数(`chat_provider_keys` 等)+ `get_chat_protocol(key)`(未知 key → 默认 qwen 协议,复刻 factory 现有 else 分支)+ `resolve_generation_protocol(actual_provider)`。
- `<key>.py`(qwen/openai/claude/deepseek/doubao/modelscope/ark/jimeng)— 每协议一个类,元数据在顶部、adapter 类**惰性 import 在 build 方法内**(零 load-time cycle,复刻现有 modelscope/RotatingAdapter 惰性 import 范式)。

### 契约保持(铁律)

- 行为**逐字节不变**:`factory.get_adapter_for_key/get_adapter_for_user/get_adapter/resolve_provider_key/provider_key_for_model/_PROVIDER_KEYS/ProviderNotConfiguredError` 全部保签名/保语义。
- 多 key `RotatingAdapter` 逻辑留在 `factory._build_adapter_for_key`(跨协议共享),只把单 key 的 per-provider 构造委托给 `protocol.build_chat_adapter(model, creds)`。
- `db_registry` 保留选行(jimeng-first)+ enabled-rows,构造委托给协议;`_ARK_PROVIDERS/_JIMENG_PROVIDERS` 退役(改用协议 `generation_family` 判定)。
- 回归网证明零漂移:`test_catalog_provider_dispatch` / `test_adapter_factory_byo` / `test_ai_library_chat_wiring` / `test_script_ai_adapter_dispatch` / image·video 测试全部保持绿,外加每协议 build 方法单测。

### Phase 2 验收

- `grep -n "if provider_key ==" factory.py` 不再有 per-provider 分支;
- 加一个新协议只需新增 `provider_protocols/<key>.py` + 注册,`factory.py`/`db_registry.py` 不改;
- 全部既有 AI 分发测试保持绿(证明行为不变)。
