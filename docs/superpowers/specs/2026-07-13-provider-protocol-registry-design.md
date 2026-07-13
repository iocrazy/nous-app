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
`app/api/admin/mediahub_model_router.py`,admin-gated,只读):

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
