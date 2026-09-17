# 抖音解析链迁移：HTTP ABogus + Camoufox

2026-09-16 · 实施规格

> 给 Claude Code：请直接在本仓库实施、测试并完成真实请求验收，不要只输出计划。
> 不要覆盖工作区中的无关改动。真实请求未通过时不得声称完成，也不得用 mock 或单元测试
> 通过代替线上请求验收。

## 1. 项目与参考实现

目标项目：

```text
/media/heygo/program/projects-code/repos/nous-app
```

已通过真实服务端验收的 HTTP 参考实现：

```text
/media/heygo/program/projects-code/_playground/douyin_parse
```

Camoufox 源码：

```text
/media/heygo/program/projects-code/github-repos/camoufox
```

目标解析链：

```text
HTTP ABogus + Argus webSign → Camoufox
```

替换当前链路：

```text
ABogus → DrissionPage
```

HTTP 路线优先。HTTP 路线成功时不得启动浏览器；只有 HTTP 路线失败时才回退 Camoufox。

## 2. HTTP ABogus 与 Argus webSign

参考以下文件：

```text
app/services/media/parsers/douyin_parse/abogus_parser.py
app/services/media/parsers/douyin_parse/websign_env.js
app/services/media/parsers/douyin_parse/websign_runtime.js
app/services/media/parsers/douyin_parse/failures.py
```

不要整文件覆盖 Nous 的 `abogus_parser.py`。先比较差异，并保留 Nous 已有的：

- `cookies_repository`
- Redis Cookie 缓存
- `safe_async_client` 与 SSRF boundary
- `safe_popen_kwargs`
- 日志、异常和进程生命周期
- 统一解析链接口

必须迁移的签名链：

1. 使用现有 Python 或 Node 引擎生成 `a_bogus`。
2. 从登录 Cookie 中读取 `UIFID`。
3. 使用原始 `webSignUrl` VM runtime 对带 `a_bogus` 的完整 URL 签名。
4. 签名结果必须包含：
   - `timestamp`
   - 查询参数 `uifid`
   - `x-secsdk-web-signature`
5. 最终详情请求头必须包含：
   - `Cookie`
   - `User-Agent`
   - `Referer`
   - `uifid`
6. Cookie、UIFID 和 User-Agent 必须在 `a_bogus`、`webSignUrl` 与最终请求之间保持一致。
7. 缺少 UIFID、Node 执行失败、签名字段缺失及 Argus 拒绝必须成为明确失败，不能静默伪装成普通空结果。
8. 日志、测试输出与异常中不得泄露 Cookie、UIFID 或完整签名。

HTTP 依赖要求：

- 将 `httpx` 依赖改为 `httpx[socks]>=0.28.1`，确保安装 `socksio`。
- 更新 `backend/uv.lock`。
- 确认运行时 Node.js 支持 `websign_env.js` 使用的 `--permission`。
- Node 不支持时升级 Node，不得静默移除子进程权限限制。
- Docker、NAS 和本地开发环境必须提供相同的 Node 运行时。

## 3. Camoufox 浏览器解析器

新增：

```text
backend/app/services/media/parsers/douyin_parse/camoufox_parser.py
```

实现 `CamoufoxParser`，要求：

1. 从 `cookies_repository` 读取用户抖音 Cookie。
2. 在首次导航前向 `.douyin.com` 注入 Cookie。
3. 使用统一解析链传入的 User-Agent。
4. 打开分享链接并解析目标 `aweme_id`。
5. 监听包含 `aweme/detail` 或 `aweme/post` 的响应。
6. 返回响应中的 `aweme_detail`，或从 `aweme_list` 选择与目标 `aweme_id` 匹配的项目。
7. 检测 `verifycenter`、captcha iframe、滑块容器和验证页面；命中后快速返回明确的挑战失败，不得长时间空等。
8. 导出有效的 Douyin Cookie，并写入现有 Redis key：
   `douyin_browser_cookies:{aweme_id}`。
9. Redis TTL 保持 600 秒。
10. 支持 `settings.SSRF_PROXY_URL`，不得绕过 Nous 的 SSRF boundary。
11. 应用退出时正确关闭 Camoufox browser/context。
12. 生产代码直接调用 Camoufox Python API，不得调用 `camoufox-reverse` MCP server。

实施前先读取本机 Camoufox 源码与实际 API，不得凭印象编造调用方式。MCP 只用于开发和逆向
取证，不是生产运行时依赖。

## 4. 删除 DrissionPage 运行时

完成以下迁移：

- 删除 `backend/app/services/media/parsers/douyin_parse/drissionpage_parser.py`。
- 删除 `drissionpage` Python 依赖并更新 lockfile。
- 将 `parse_chain.py` 改为 `ABogus → Camoufox`。
- 方法值由 `abogus | drissionpage` 改为 `abogus | camoufox`。
- 将 `douyin_drissionpage_enabled` 迁移为 `douyin_camoufox_enabled`。
- 新增数据库 migration，迁移旧设置的值；不得修改已经应用的历史 migration。
- 若 `settings_json` 中存在 `"parse_mode":"drissionpage"`，迁移为 `"camoufox"`。
- 更新 teardown、服务导出、下载重解析、SSRF boundary、测试和运行时文档。
- 全仓搜索 `DrissionPage` 和 `drissionpage`，删除运行时遗留、无用导入和过时注释。
- 不保留兼容类名、兼容模块、双实现或废弃代码。

历史 migration 和历史设计文档可保留原文；生产运行时代码不得继续引用 DrissionPage。

## 5. 自动化测试

### 5.1 HTTP signer

- 缺少 UIFID 时明确失败。
- `webSignUrl` 返回 URL 必须包含 `x-secsdk-web-signature`。
- 最终 HTTP 请求头必须包含 `uifid`。
- Cookie、UA 和 UIFID 必须正确传给 Node 子进程。
- Node 失败与 Argus 拒绝必须被正确分类。
- 日志及异常不得泄露 Cookie。

### 5.2 统一解析链

- ABogus 成功时不启动 Camoufox。
- ABogus 返回空时回退 Camoufox。
- ABogus 抛错时回退 Camoufox。
- Camoufox 成功时返回 `method="camoufox"`。
- 两条路线都失败时返回 `None`。
- `douyin_camoufox_enabled` 开关生效。
- 运行时代码不存在 DrissionPage 导入。

### 5.3 Camoufox

- Cookie 在导航前注入。
- 能捕获 `aweme/detail`。
- 能从 `aweme_list` 选择正确的 `aweme_id`。
- 验证码出现时快速终止。
- 浏览器 Cookie 能写入原 Redis key，TTL 为 600 秒。
- `SSRF_PROXY_URL` 被正确配置到浏览器。
- teardown 能释放 browser/context。

## 6. 真实请求验收

测试链接：

```text
https://v.douyin.com/-eB_iaHFsoU/
```

目标 `aweme_id`：

```text
7684532239228740849
```

### 6.1 强制 HTTP ABogus

必须满足：

- 不启动浏览器。
- 详情接口返回 HTTP 200。
- JSON `status_code=0`。
- 返回目标 `aweme_id`。
- 播放或下载地址非空。

### 6.2 强制 Camoufox

暂时禁用 ABogus，或直接调用 `CamoufoxParser`。必须满足：

- 成功捕获目标 `aweme_detail`。
- 返回目标 `aweme_id`。
- 播放或下载地址非空。
- 不加载 DrissionPage 或 Chrome。
- 没有验证码阻塞；若出现验证码，必须报告为真实验收失败。

### 6.3 完整解析链

- 顺序必须是 `ABogus → Camoufox`。
- HTTP 成功时不得创建 Camoufox 进程。
- HTTP 失败时 Camoufox 能被实际调用并返回结果。

## 7. 完成条件

以下条件全部满足后才能声称完成：

1. HTTP ABogus 真实请求验收通过。
2. Camoufox 真实浏览器验收通过。
3. 完整链及相关自动化测试通过。
4. `pyproject.toml`、`uv.lock`、Docker/NAS 部署依赖同步完成。
5. 生产运行时代码不再引用 DrissionPage。
6. 没有泄露 Cookie、UIFID 或签名。

完成后报告：

- 修改、增加和删除的文件。
- 依赖及部署变化。
- 自动化测试命令和结果。
- HTTP ABogus 真实验收结果。
- Camoufox 真实验收结果。
- 尚未解决的风险。

若任何真实验收没有通过，必须给出失败阶段、原始错误的脱敏摘要和下一步，不得用“基本完成”
或“单元测试通过”替代交付结论。
