# nous-engine 图片桥（画布「放大」）

画布放大（`POST /api/v1/generated-media/{id}/upscale`）按「nous-engine 优先、即梦兜底」选后端：
取调用者可见的、已启用的 `type='image'` 目录行，只留协议声明 `upscale_capable` 的
（`nous`、`jimeng-cli`），nous 排前，同族内按 `sort_order`。选中后**不做运行时回退**：
nous 拒绝（未授权、忙）就直接报错，不悄悄换即梦跑——那会掩盖配置问题、还换了计费账户。

## 目录行

| 字段 | 值 |
|---|---|
| `name` | `nous-studio-upscale`（mig 497 从已有 nous 平台行复制 key 与 base_url） |
| `actual_provider` | `nous` |
| `actual_model` | `studio-upscale`（nous-engine 已发布服务名） |
| `base_url` | 同其他 nous 行，如 `http://host.docker.internal:8000/v1` |

`nous` 族的图片行当前**只做放大**（`text_to_image = False`）：不进画布出图的模型选择器，
也不会被文生图的默认挑选选中；显式点名它去文生图会报 "upscale-only service"。
将来 nous-engine 上线文生图服务时，需要单独的族 key 或行级标记，不能直接翻这个开关。

## 前提：在 nous-engine 后台授权

鉴权是 InstanceApiKey（目录行的 `api_key`，Bearer）。这把 key 必须在 nous-engine
后台被授权使用 `studio-upscale`，否则引擎回 404 `model_not_found`。

验证授权的探针（在后端容器里跑，key 从目录行取）：

```bash
curl -sS -H "Authorization: Bearer $KEY" "$BASE_URL/models?type=image"
```

列表里有 `studio-upscale` 才算授权到位。Admin 的「测试」按钮对 nous 图片行显示
`not_probed` 是预期的：真探一次等于跑一次 GPU 放大，还需要输入图。

## 请求形状

```json
{"model": "studio-upscale", "image": "data:image/png;base64,...", "resolution": 2160}
```

响应 `data[0].url` 是带 `token`/`expires` 的签名链接，下载时**不带** Authorization。
产物落在 0700 的 `nousimg_*` 临时目录（0600 独占创建），登记进存储后由路由回收。

## 分辨率映射

| 画布传入 | 目标短边（px） |
|---|---|
| `2k` | 1440 |
| `4k` | 2160 |
| `8k` | 4320 |

其他值直接报错，不默认。

## 错误码

| HTTP | `error.code` | 含义 / 处理 |
|---|---|---|
| 404 | `model_not_found` | key 未授权该服务，去 nous-engine 后台授权 |
| 402 | 配额类 | key 配额用尽 |
| 503 | 未就绪类 | 服务在加载或 GPU 忙，稍后重试 |
| — | `no_url` | 2xx 但没有图片链接，属引擎缺陷 |
| — | `download_failed` | 签名链接下载失败（常见是过期） |

路由把上游失败转成 502，没有可用后端转成 503。生产错误外壳会把 5xx 响应体统一成
"Internal server error"，所以**后端名、状态码、`error.code` 只在日志里**，形如：

```text
upscale failed via nous-studio-upscale [status=404, code=model_not_found]: ...
```

查法：`application_logs` 里搜 `upscale failed via`。
