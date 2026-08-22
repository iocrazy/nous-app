# 发布配乐选择器（抖音）· 勘察与设计

2026-08-15 · 起因：用户是自媒体作者，发抖音时想像官网那样**在侧边栏选配乐**。当前发布页
`music_name` 是一个自由文本框（`PublishPage.tsx` 的 `musicName` → 后端 `music_name` →
`platform_options.music` → 浏览器侧 `_set_music` 拿这个字符串去平台的音乐弹窗里搜并点中）。
用户看到的就是「让我手打歌名」，他的原话是「连音乐都没有」。

本文只做勘察与设计，不含实现。

> **本轮最重要的两个结论，先说**：
> 1. **搜索能做**。几天前「`tsearch` 恒 403 = 边缘层直接拒」的结论**是错的**——403 的原因是
>    缺一个 `Agw-Auth` 头，而那个头能从 creator 站自己的一个带 cookie 的接口换到。补上之后
>    同一个 URL 200 且返回真结果（§1.4）。
> 2. **但选择器本身会把一个既有缺陷放大成事故**。曲名**不唯一**（实测「起风了」一次搜索里
>    5 条同名不同 id），而分类榜里的曲子**搜不到**（实测 3/3 未命中，其中一条还返回了一个
>    同名的**别的** id）。现有 `judge_music_choice` 按曲名 exact 匹配、匹配上就报
>    `match="exact"` ——用户以为自己挑了一首具体的歌，系统却会**满怀信心地发出另一首**。
>    这是设计里必须先解决的东西，不是发完再优化的东西（§3）。

---

## 0. 现状盘点（仓库实据）

| 位置 | 现状 |
|------|------|
| `frontend/components/Distribution/PublishPage.tsx:375` | `musicName` 自由文本 state；`:2071` 一个 `<input placeholder="Track name to search">` |
| `PublishPage.tsx:642` | `musicSupported` = 全部目标账号 `capabilities[platform].supports_music`，否则整行不渲染 |
| `backend/app/schemas/distribution_publish.py:182` | `music_name: Optional[str]`，`normalize_music` 清洗，**无白名单**（注释明写"曲名不是封闭词表，存在性由浏览器侧在平台自己的搜索结果里判定"） |
| `publish_gate.py:137` / `publish_distribution.py:373` | 非空才进 `platform_options["music"]`；`None` = 不碰控件 = 平台默认原声 |
| `backend/app/models/distribution.py:225` | `publish_tasks.music_name TEXT`（mig 425） |
| `browser/app/platforms/douyin_publish.py:2055` `_set_music` | 开弹窗 → 在**平台自己的搜索框**里填曲名 → 回车 → 读结果行 → `judge_music_choice` 决定点哪行 → 读回确认。任何一步失配都 `raise`（`music_entry_missing` / `music_search_missing` / `music_not_found` / `music_click_failed` / `music_dialog_stuck` / `music_not_confirmed`） |
| `douyin_publish.py:865` `judge_music_choice` | **exact 优先，否则取第一条**（`match` ∈ `exact` / `approximate` / `none`） |
| `douyin_publish.py:356-383` | T0 实测记录：弹窗有搜索框（placeholder `搜索音乐`）+ tab 行（推荐/热门榜/收藏/飙升榜/原创榜/卡点…）+ 结果行 `歌名` + `作者·时长` + `N万人使用`。⚠️ 行的 class、是否需「使用」按钮**未实测** |

**没有**任何音乐选择的实现，也没有 spec。参照实现是
`backend/app/services/distribution/topic_suggest.py`（话题建议）：单一出网点、类型化失败
`raise`、进程内短 TTL 缓存、匿名查询不绑账号。本设计沿用它的骨架，差异见 §4。

---

## 1. 接口事实表（2026-08-15 重验）

验证方式：`docker exec -i nous-backend /app/.venv/bin/python -`（stdin 喂脚本，容器内不落文件），
会话从 `social_accounts.session_state` 经 `secret_box.decrypt` **在进程内**解出，cookie 从未
离开容器、从未打印。生产库只读。

### 1.0 两个可复现的判据（先立，后面每条结论都靠它）

creator 站对**不存在的路由**回 `{"status_code":1,"status_msg":"Url doesn't match"}`，对**存在但
未登录**回 `{"status_code":8,"status_msg":"用户未登录"}`——两者都是 **HTTP 200**。

⚠️ **这意味着「HTTP 200」在这条链上完全不构成成功信号**。任何实现里判断成功都必须看
`status_code == 0`，把 200 当成功会把"未登录"和"路由不存在"一起吞成静默空列表——正是本仓库
`attachment_failures` 那一族的老问题。

拿一个确定不存在的路径做过对照（`/web/api/media/music/nonexistent_control` → `status_code:1`），
所以下面每条"路由存在"都是被这个判据证过的，不是猜的。

### 1.1 事实表

| # | 接口 | 方法 | 鉴权 | 结论 | 证据等级 |
|---|------|------|------|------|---------|
| A | `creator.douyin.com/web/api/media/music/category` | GET | **cookie 必需** | ✅ 通，返回 12 个分类 | **实测** |
| B | `creator.douyin.com/web/api/media/music/list` | GET | **cookie 必需** | ✅ 通，按 tab 返回曲目 | **实测** |
| C | `creator.douyin.com/web/api/media/aweme/search/post/auth` | GET | **cookie 必需** | ✅ 返回 `signature`（121 字符） | **实测** |
| D | `tsearch.amemv.com/openapi/aweme/v1/music/search/` | GET | **`Agw-Auth` 头必需，cookie 不需要** | ✅ 通（旧结论"恒 403"已推翻） | **实测** |
| E | `creator.douyin.com/web/api/media/music/info/` | GET | cookie 必需 | ✅ 按 id 查曲目详情 | **实测** |
| F | `creator.douyin.com/web/api/media/music/validate/` | GET | cookie 必需 | ✅ 传**抖音音乐分享链接**换 id（不是校验 id） | **实测** |

⚠️ **A/B 的路径没有尾斜杠**。带尾斜杠会 301 到无斜杠版（`Moved Permanently`，HTML 响应体）；
如果客户端不跟随重定向、又只看 HTTP 状态，会得到一个"非 200 但也不是错误"的中间态。写死无斜杠。

### 1.2 A · 分类（tab 列表）

```
GET https://creator.douyin.com/web/api/media/music/category?aid=2906
Cookie: <账号会话>
```

返回（实测原样，12 条）：

| category_name | type | 说明 |
|---|---|---|
| 推荐 | `recommend` | |
| 热门榜 | `rank` | |
| **收藏** | **`fav`** | **平台侧收藏，按账号** |
| 飙升榜 | `rank` | |
| 原创榜 | `rank` | |
| 卡点 / 纯音乐 / 旅行 / DJ / 搞笑 / 流行 / 伤感 | `category` | 7 个内容分类 |

**用户列的五个标签，平台自己一个不少地给了**，而且分类列表是平台下发的（不是我们写死的），
以后平台加减分类我们跟着变。

### 1.3 B · 曲目列表

```
GET https://creator.douyin.com/web/api/media/music/list
    ?aid=2906&category_id=<A 给的>&type=<A 给的>&cursor=0&count=20
```

- 成功体：`{status_code:0, songs:[...], cursor, has_more}` — **顶层键是 `songs`**，不是
  `music_list`。
- 每条：`cover_url` / `music_name` / `music_author` / `duration`(秒) / `user_count`(整数) /
  `music_id`(**JSON 字符串**) / `play_url`。
  **用户要的五个显示字段（封面、歌名、作者、时长、使用量）一个不缺**，且 `play_url` 还白送了试听。
- 分页：`cursor` + `has_more` 实测可用，第 2 页与第 1 页 **0 重叠**。
  ⚠️ `cursor` 语义**按 tab 不同**：`rank`/`category` 是偏移量（`0→20→40`），`fav` 是一个
  时间戳式游标（如 `1762790314736071`）。实现必须原样回传上一页给的 `cursor`，不能自己算。
- `count` 上限 20：传 `count=100` 仍只回 20（`status_code=0`，静默截断）。
- `aid` 在带 cookie 时**不必需**（去掉照样 `status_code=0`）；匿名时必需也没用（照样 8）。
- ⚠️ **不支持搜索**。`keyword` / `search_key` / `query` / `q` / `music_name` / `search_keyword`
  六种参数名全部被**静默忽略**（返回的仍是该 tab 的常规列表，`status_code=0`）；`type=search`
  直接 `status_code=4`。这条必须写死在实现的注释里——"参数被忽略但接口返回成功"是最容易被
  误当成"搜到了这些"的形态。

### 1.4 C+D · 搜索（旧结论推翻）

几天前记的「`POST tsearch.amemv.com/...` 恒 403，边缘层直接拒」**是对症状的正确观察、对原因的
错误归因**。真实调用链（从 creator 前端 bundle 里读出来的，不是猜的）：

```js
// douyin-creator-content-new/micro/static/js/async/6254.f76d45b9.js（实测抓到的原文，已展开）
const {signature} = await getAuth();            // GET /web/api/media/aweme/search/post/auth（带 cookie）
return axios.get("https://tsearch.amemv.com/openapi/aweme/v1/music/search/", {
  withCredentials: false,                        // ← 搜索本身不带 cookie
  params: {aid: DOUYIN, count: 20, search_source: "normal_search", ...args},
  headers: {"Agw-Auth": signature, "Openapi-Omit-Shark": "1"}
});
```

实测验证（三档对照，可复现）：

| 请求 | 结果 |
|---|---|
| 无 `Agw-Auth` | **403**（空响应体）← 这就是旧结论看到的东西 |
| `Agw-Auth: bogus` | **403** |
| `Agw-Auth: <真 signature>` + `aid=2906` | **200**，但 `music:[]` 且 `search_nil_info.search_nil_item = "invalid_app"` |
| `Agw-Auth: <真 signature>` + **`aid=1128`** | **200，真结果** |

⚠️ `aid` 这一档尤其值得记：**换错 aid 不报错，只是永远返回空列表**——又一个"看起来能搜、
就是搜不出东西"的形态。`1128` 是抖音 App 的 aid（`2906` 是 creator 站的，用在 A/B 上）；同一
条链上两个 aid 各管一段，不能统一。

`signature` 的性质（实测）：连续两次调用 C **返回同一个值**，长度 121。所以它是可缓存的，
不是一次性 nonce。⚠️ 但**没有测出它的 TTL**（要么等，要么反解，本轮都没做）——实现必须把它当
"随时会过期"处理：403 就重取一次 signature 再重试一次，重试仍 403 才算失败。

搜索结果每条的可用字段：`id_str` / `title` / `author` / `duration` / `user_count` /
`cover_hd|cover_large|cover_medium|cover_thumb`（`{uri, url_list[], width, height}`）/ `play_url` /
`is_commerce_music` / `music_status` / `is_original` …

⚠️⚠️ **`id` 是 JSON number 且超过 2^53**（实测 `6953836671917951012`）。必须用 `id_str`。
这正是 CLAUDE.md「Snowflake BIGINT 精度丢失」那一条的外部版本——差别是这次连后端也会踩（Python
的 int 没事，但一旦这个值经 JSON 进前端就废了）。**边界 mock 必须照抄这个形状**：`id` 是 number、
`id_str` 是 string，两者并存且**不能"统一"**。

搜索的失败/空形态（实测）：
- 上游业务码 `status_code` + `search_nil_info.search_nil_item`（`invalid_app` = 参数不对）。
- 真·搜不到的情况很少：拿一个乱码关键词（`zzzqqqxxxnosuchtrack123`）搜，仍回 **8 条**结果。
  也就是说这个搜索是**模糊召回**，"空列表"几乎只会因为参数错，不会因为没这首歌。
  ⚠️ 这条直接决定 UI 文案：不能把空列表说成 "No results"，那多半是我们自己参数错了。
- `count=10` 实测只回 6 条——**上游自己会裁**，`count` 是上限不是数量。

### 1.5 E · 曲目详情

```
GET https://creator.douyin.com/web/api/media/music/info/?aid=2906&id=<music_id>
```

⚠️ 参数名是 **`id`**，不是 `music_id`（传 `music_id` 得 `status_code:5 参数不合法`）。
返回 `{status_code:0, music_id, music_name, music_author, cover_url, is_pgc}`。

**关键的一次交叉验证**：把 D（tsearch）搜出来的 `id_str` 喂给 E（creator 站），**5/5 全部
`status_code=0` 且曲名一致**。所以搜索侧和发布侧是**同一个 id 命名空间**——搜索结果不是另一个
世界的东西。这是"搜索可用于发布"的直接证据，不是推断。

### 1.6 F · 链接换 id（本轮附带发现，非用户需求）

`/web/api/media/music/validate/?url=<抖音音乐分享链接>` → `{music_id, music_name, music_author,
cover_url}`。这是发布页「粘贴音乐链接」那条路径。本期不做，记在这里是因为它是 Phase 3 的现成
入口（用户从抖音 App 分享一首歌过来）。

### 1.7 明确没查清的（不要当结论用）

| 项 | 状态 |
|---|---|
| `signature` 的 TTL | **未测**。按"随时过期"实现 |
| 弹窗结果行的 DOM 结构（class、是否需「使用」按钮、行上有没有 music id 属性） | **未测**（T0 就没测，本轮也没测——要测得真开一次编辑器，那要先传一个素材、会在平台留一条草稿） |
| 视频编辑器与图文编辑器的音乐弹窗是否同一套文案 | **未测**（`douyin_publish.py:379` 已记：只在图文编辑器上量过） |
| 各 tab 的曲子是否都能在发布弹窗里被选中 | 部分实测，结论很糟，见 §3 |
| 平台是否有"上传自有音频"的入口 | **未测到任何入口**，但这是"没找到"不是"不存在"，见 §5 |

---

## 2. 用户要的五个标签：逐个结论

| 用户要的 | 结论 | 理由 |
|---|---|---|
| **搜索框** | ✅ **能做** | §1.4 实测通。旧结论"恒 403"已被推翻，原因是缺 `Agw-Auth` 头 |
| **推荐** | ✅ 能做 | `type=recommend`，实测 20 条/页 |
| **热门榜** | ✅ 能做 | `type=rank`，实测 20 条/页 + 分页 |
| **收藏** | ✅ **能做，且是平台侧的真收藏** | `type=fav`。**按账号隔离**已实测：三个 active 会话账号分别拿到 0 / 18 / 9 条，互不相同。所以它是平台上那个账号自己收藏的歌，不是我们要自己存的东西 |
| **飙升榜** | ✅ 能做 | `type=rank`（另一个 `category_id`） |
| **原创榜** | ✅ 能做 | `type=rank`（另一个 `category_id`） |

**五个全部能做，一个都不用降级。** 而且平台还多给了 7 个内容分类（卡点/纯音乐/旅行/DJ/搞笑/
流行/伤感），分类表是接口下发的，UI 直接渲染返回值即可，不写死。

⚠️ 「收藏」有一个必须在 UI 上说清楚的性质：它**属于某一个抖音账号**。发布页可以同时选多个
账号，这时"收藏"是谁的收藏？见 §4.3——这不是技术问题，是必须先拍板的语义问题。

---

## 3. 真正的难点：选中之后传什么（以及为什么现在的做法会选错歌）

### 3.1 缺陷的机理

现在这条链是**按曲名**走的：前端存字符串 → 后端存字符串 → 浏览器把字符串填进平台搜索框 →
`judge_music_choice` 在结果里找**曲名相等**的那行，找到就点，并报 `match="exact"`。

在"用户手打歌名"的世界里这是合理的——用户本来就只知道一个名字，取哪个同名版本都算满足意图。

**但选择器把这个前提废掉了**：用户点的是一张有封面、有作者、有时长、有使用量的**具体卡片**。
这时候"发出去的是同名的另一首"就不再是近似满足，而是**发错了**。

两组实测数据说明这不是理论风险：

**① 曲名根本不唯一。** 搜「起风了」，一页里 5 条标题**完全相同**（`起风了`），id 各不相同，
使用量从 0 到 30023 不等。用户挑的是 30023 人用的那首（这正是他挑它的理由），
`judge_music_choice` 会取**第一条同名的**——两者是不是同一首，纯看运气。

**② 分类榜里的曲子搜不到。** 拿「卡点」分类里的 3 首曲子，用它们自己的曲名去搜：

| 曲名（分类列表里的） | 搜索能否命中同一个 id | 搜索结果里同名的行 |
|---|---|---|
| 花落花（副歌版） | ❌ 19 条里没有 | 0 条 |
| 电子布洛芬（Live） | ❌ 19 条里没有 | **1 条（是别的 id）** |
| Talk like me(像我这样说)（Live） | ❌ 15 条里没有 | 0 条 |

对照：热门榜的 3 首**都能命中**（排在第 0、0、1 位）。

第二行是最坏的组合：用户在「卡点」tab 点了《电子布洛芬（Live）》，浏览器拿这个名字去搜，
搜到一行**标题一模一样但 id 不同**的歌，`judge_music_choice` 判 `exact`，读回校验也会过
（页面上确实显示了这个名字），**整条链每一道关卡都亮绿灯，发出去的是另一首歌**。

⚠️ 这正是本仓库反复栽的那一族："证据看起来成立，但它证明的不是我们以为的那件事"。
`_music_mentions` 读回校验是很好的设计，但它只能证明"页面上有这个名字"，**不能证明"是用户
选的那一首"**——因为名字不是身份。

### 3.2 三个候选解法

| 方案 | 做法 | 能不能真选中用户挑的那首 | 代价 |
|---|---|---|---|
| **① 只让用户挑"搜得到"的** | 选择器只暴露**搜索结果**（不暴露榜单/分类/收藏），选中后仍按名传 | 部分。同名歧义仍在（实测 5 条同名） | 砍掉用户明确要的四个 tab，**不可接受** |
| **② 传结构化"指纹"，浏览器按多字段对齐** | 存 `music_id` + `music_name` + `music_author` + `duration` + `user_count`；`_music_rows` 从只读第一行文本扩展到读全部三行（`歌名` / `作者·时长` / `N万人使用`，**都是 T0 已实测的文案**），`judge_music_choice` 改成按 (名, 作者, 时长) 三元组对齐，只在唯一命中时判 `exact` | 同名歧义**能解**（作者+时长足以区分实测那 5 条）。榜单/分类曲搜不到**不能解**——搜索里根本没那行 | 改 `douyin_publish.py` 两个纯函数 + 一个 JS 提取器。纯函数可单测 |
| **③ 浏览器改成走 tab，而不是走搜索框** | 用户在哪个 tab 挑的，浏览器就点开哪个 tab、翻页找到那一行 | 能解全部 | 依赖弹窗 tab 的 DOM（**完全未实测**）+ 翻页成本（20 条/页，用户挑的可能在第 5 页）。这是**目前最不该先做的**：它把整条链押在一批我们一次都没量过的选择器上 |

### 3.3 推荐

**② + 一条硬约束**：

- 采纳 ② 的结构化指纹（存 id 与全部展示字段，浏览器按三元组对齐）。
- **Phase 1 的选择器只上"搜索"这一个入口**，四个 tab 在 UI 上出现但**先不做**（见 §6 分期）。
  理由不是技术，是诚实：榜单/分类的曲子我们**已经实测知道**发布时可能选不中，先上等于明知
  会错还发。
- `music_id` 从第一天就存下来。它现在还不能当选择器用（弹窗行上有没有 id 属性未测），但
  它是唯一真正的身份，且它**已经被证明在发布侧可解析**（§1.5 交叉验证）。等哪天真去量了
  弹窗 DOM，有没有这个字段决定了方案 ③ 是一周还是从头再来。
- **失败必须比现在更严，不是更松**：三元组不唯一命中时，`judge_music_choice` 返回
  `ambiguous` 并让 `_set_music` **raise**（新增 reason `music_ambiguous`）。理由和该函数
  docstring 里已经写下的一致——配乐发出去改不了，而"发了但是错的歌"比"没发"更坏，因为它
  不会有任何信号。⚠️ 注意这与现有 `approximate` 档的取舍方向相反，是**故意的**：手打歌名时
  近似是善意补全，点了卡片之后近似就是发错。两条路径应该有两套判定，不是一套。

---

## 4. 拉取时机、缓存与风控

### 4.1 这条链比 `topic_suggest` 危险，不能照抄它的松弛度

`topic_suggest` 是**匿名**公共查询（无 cookie、无签名），所以它可以随便打。本链不是：

- A/B/C/E 全部**必须带账号 cookie**——每一次请求都算进那个账号的风控画像。
- D 虽然不带 cookie，但它的通行证来自 C，等于间接绑定账号。

所以本仓库既有的风控克制约定（`publish_readback.MIN_RETRY_INTERVAL_S = 30min` 那一族）在
这里适用，且要更谨慎：**打开发布页就去拉一次列表**是不可接受的——用户开十次发布页就是十次
带身份的请求，而其中九次他根本没点音乐。

### 4.2 缓存策略

| 数据 | 缓存位置 | TTL | 键 | 理由 |
|---|---|---|---|---|
| 分类表（A） | 进程内 | **24h** | 全局（不含账号） | 12 个分类，几个月不动。它不含任何账号数据，可以全局共享 |
| 榜单/分类曲目（B，`type=rank`/`category`） | 进程内 | **30min** | `(type, category_id, cursor)`，**不含账号** | 榜单对所有账号一样。⚠️ 这是刻意的**去账号化**：一次拉取服务所有用户，把平台请求量从 O(用户数) 压到 O(1)。请求本身仍要借某个账号的 cookie 发出，但结果不按账号存 |
| 收藏（B，`type=fav`） | 进程内 | **5min** | `(account_id, cursor)` | 用户刚在抖音收藏完就切过来，5 分钟已经是能忍的上限。**必须按账号**，它就是账号私有数据 |
| 搜索结果（D） | 进程内 | **5min** | `(keyword, cursor)`，不含账号 | 同 `topic_suggest` 的 `CACHE_TTL_SECONDS=300`。搜索结果与账号无关（`withCredentials:false`） |
| `signature`（C） | 进程内 | **10min**（保守值，TTL 未知） | 全局 | 实测两次调用同值 → 可缓存。403 时**作废并重取一次**，见 §1.4 |

三条硬约束：

1. **只缓存成功**（沿用 `topic_suggest` 的注释原话："缓存一次失败 = 把一次抖动放大成 5 分钟的
   功能不可用"）。
2. **缓存必须有条目上限**（`topic_suggest` 的 `CACHE_MAX_ENTRIES=512`）——搜索键来自用户输入，
   没有上限就是一条按输入无限增长的内存路径。
3. **懒加载**：侧栏音乐面板**不打开就一个请求都不发**。发布页初次渲染只需要知道
   `supports_music`，那是已有的能力声明，不用问平台。

### 4.3 借哪个账号的 cookie？（必须先拍板）

A/B/C/E 要 cookie，而发布页可以同时选 N 个账号。三种口径：

| 口径 | 说明 | 问题 |
|---|---|---|
| 用第一个目标账号 | 实现最省事 | "收藏"显示的是谁的收藏完全看账号排序，用户不可预期 |
| **让用户显式选一个"浏览身份"** | 面板顶部一个账号选择器，默认第一个 | 多一个控件，但**语义是准的**：收藏本来就属于某个账号 |
| 用一个固定的系统账号 | — | ❌ 直接否决：拿 A 账号的会话去服务 B 用户，是跨租户 |

**推荐第二种**，且只在面板里出现（不影响发布本身选了哪些账号）。榜单/分类/搜索的结果与身份
无关，所以换身份只影响"收藏"这一个 tab——UI 上应该这样说，而不是让用户以为整个面板都变了。

---

## 5. 「打通我们自己的音乐库」这条退路，值多少

用户问过：如果难，能不能用他素材库里的音频。认真评估：

**它不成立为"替代方案"，但成立为"另一件事"。**

技术上（**推断，非实测**，证据在下面）：抖音发布页的音乐控件只能从平台曲库里选。已实测到的
三条入口——搜索、分类/榜单列表、粘贴抖音音乐链接（§1.6）——**全部指向平台曲库里的 id**；T0
那次编辑器走查也没量到任何"上传音频"的控件。⚠️ 这是"没找到入口"，不是"证明没有"，但三条入口
一致指向同一个封闭曲库，是相当强的旁证。

如果要用自有音频，唯一的路是**发布前把音频混进视频**（ffmpeg，backend 已有 ffmpeg 依赖，
`resources` 里也已有音频资源：`music_download_path` / `extract_audio_path` / `audio.mp3`）。
这条路技术上完全可行，但它做的**不是同一件事**：

| | 选平台配乐 | 自有音频混进视频 |
|---|---|---|
| 作品底部的音乐信息 | 平台曲目，可点进去 | 「@用户 创作的原声」 |
| 「使用同款音乐」的流量入口 | ✅ 有 | ❌ 没有 |
| 曲库范围 | 平台曲库 | 任意 |
| 版权 | 平台已授权 | 自负 |

`_set_music` 的 docstring 里已经把这件事说清楚了："用户输入曲名**正是因为**发在原声上分发更
差——那是这个字段存在的全部理由"。**自有音频得到的恰恰就是原声**。所以拿它替代配乐选择器，
等于把用户要的东西原样还给他并告诉他已经做好了。

**结论**：这条退路**不需要作为退路**——主路已经实测可行（§1、§2 五个标签全绿）。自有音频
应该单独立项（"给视频配自己的 BGM"），跟"发抖音时选配乐"是两个需求，不要混。

---

## 6. 推荐方案与分期

### 方案 A（推荐）：搜索优先，榜单押后

**Phase 1 — 搜索 + 结构化选择**

- 后端：新增 `services/distribution/music_catalog.py`，仿 `topic_suggest` 的骨架：
  - **单一出网点**（改签名/加风控时只改一处）；
  - 失败**全部类型化 raise**（`REASON_UPSTREAM_STATUS` / `_SHAPE` / `_UNREACHABLE` /
    `_SIGNATURE_EXPIRED` / `_NO_SESSION`），空列表是合法成功；
  - `status_code != 0` 一律当失败（§1.0）；
  - 缓存按 §4.2。
- 后端：`GET /api/v1/distribution/music/search?keyword=&cursor=&account_id=`。
- schema：`music_name` 保留（**不删，向后兼容**）+ 新增 `music_ref`
  （`{music_id, music_name, music_author, duration, user_count, cover_url}`）。
  两者共存的语义：`music_ref` 存在时以它为准，只有 `music_name` 时走今天的老路径。
  迁移加一列 `publish_tasks.music_ref JSONB`（⚠️ 取号前先 `git fetch` 看 master 上的最新号）。
- 浏览器：`_music_rows` 扩展成读三行文本；`judge_music_choice` 增加三元组对齐分支与
  `ambiguous` 档；新增失败 reason `music_ambiguous`。**老的按名路径原样保留**。
- 前端：侧栏面板，只有搜索框 + 结果列表（封面/歌名/作者/时长/使用量 + `play_url` 试听）。

**Phase 2 — 四个 tab**

前提是先做掉一件事：**真去量一次弹窗的 tab 与结果行 DOM**（在编辑器里，会留一条草稿，需要
用户点头）。量到什么决定怎么做：

- 行上有 music id 属性 → 直接按 id 点中，§3 的整个问题消失；
- 只有文本 → 走方案 ③（点 tab + 翻页），成本明显更高，但至少是有依据的实现。

**在这次测量之前不要上 tab**——因为我们**已经实测知道**分类曲搜不到（§3.1），先上就是明知会
选错还发。

**Phase 3 —**（可选）粘贴抖音音乐链接（§1.6 的 F 接口，现成）。

### 方案 B（不推荐）：五个 tab 一次做完，仍按曲名传

三周就能上，UI 和用户描述完全一致。**否决理由**：§3.1 实测证明它会静默发错歌，而且每一道
校验都会亮绿灯。这正好是本仓库这几天连修七处的那一族问题——不该再添第八处。

### 这个推荐方案**做不到**用户原话里的哪些部分（明说）

1. **Phase 1 只有搜索框，四个 tab 要等 Phase 2。** 用户要的形态是"打开就能刷榜单"，第一版
   给不了。
2. **「收藏」会绑到一个显式选择的账号**，不是"我的收藏"这种无歧义的东西——因为平台上它本来
   就属于某个抖音账号，而发布页可以选多个。
3. **自有音乐库不在计划内**，且它做不到用户想要的效果（§5）。
4. **平台随时可以让这一切失效**：这四个接口全是 creator 站私有接口，无任何契约。
   `topic_suggest` 的模块头已经把降级梯子写清楚了，本层沿用同一套姿势（单一出网点 +
   类型化失败），但**不能承诺它明天还通**。

---

## 7. 落地前必须先答的问题

1. **§4.3 的"浏览身份"口径**：面板顶部加账号选择器，还是默认取第一个目标账号？（影响"收藏"
   这一个 tab 的语义）
2. **Phase 2 的 DOM 测量要不要做**：需要在真账号上传一个素材进编辑器，会在平台留一条草稿
   （可删）。不做就没有 tab。
3. **`ambiguous` 是 raise 还是降级**：本文推荐 raise（§3.3）。它意味着"选了歌但发布失败"会
   比现在多——换来的是"绝不发错歌"。这是取舍，需要用户拍。

---

## 附：复现记录

全部经 `docker exec -i nous-backend /app/.venv/bin/python -`（脚本走 stdin，容器内不落文件），
会话在进程内解密、cookie 未打印未导出，生产库只读。

```
# 判据（匿名，可直接复现，不需要账号）
GET https://creator.douyin.com/web/api/media/music/category      → {"status_code":8,"status_msg":"用户未登录"}   ← 路由存在
GET https://creator.douyin.com/web/api/media/music/nonexistent   → {"status_code":1,"status_msg":"Url doesn't match"} ← 对照组
GET https://tsearch.amemv.com/openapi/aweme/v1/music/search/     → 403（空体）                                    ← 缺 Agw-Auth

# 带会话（三条链路，需账号 cookie）
GET /web/api/media/music/category?aid=2906                                   → 12 个分类
GET /web/api/media/music/list?aid=2906&category_id=..&type=rank&cursor=0&count=20 → songs[20]
GET /web/api/media/aweme/search/post/auth                                    → {signature: <121 chars>}
GET tsearch.../music/search/?aid=1128&count=20&search_source=normal_search&keyword=..
    headers: Agw-Auth=<signature>, Openapi-Omit-Shark=1                      → music[] 真结果
GET /web/api/media/music/info/?aid=2906&id=<搜索来的 id_str>                  → status_code=0，曲名一致（5/5）
```

前端调用链的原文出处（供将来平台改版时重新定位）：
`https://lf-fe-creator.douyinstatic.com/obj/douyn-creator-scm-cdn/douyin-creator-content-new/micro/static/js/async/6254.<hash>.js`
（音乐弹窗）与 `.../douyin_content_index_micro_new.<hash>.js`（`signature` 提供方）。
定位路径：creator 主站 bundle → Garfish 子应用 `/goofy/douyin_creator_pc/mono/creator_content`
→ 该子应用的 `builder-runtime` 里有 153 个异步 chunk 的映射表。
