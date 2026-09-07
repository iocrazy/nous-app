> **已退役（2026-09-07）**：`/f/` 签名直出、`app/services/media/nginx_direct.py`、gateway 的媒体目录挂载与 `system_settings.nginx_direct_serve` 已全部移除。前提是媒体数据全量迁入对象存储后库里不再有任何本地路径，nginx 没有磁盘可直出。下文仅作历史记录。

# nginx 静态直出(signed direct serve)— Runbook

> ⚠️ **2026-07-28 更新:NAS 老栈已退役,本文下半部分(NAS Runbook)仅存档。**
> 当前生效的是 gpupc 栈,见下节。

## gpupc 栈(2026-07-28 起,当前形态)

- 拓扑:nas-A 反代 `cn.nous.ink:88` → `10.0.0.10:8080` = **`nous-gateway`**(nginx,
  `deploy/gpu-server/nginx-gateway/default.conf.template`):`/f/` secure_link 直出
  `/mnt/heytime/Sources/nous/media`(ro),其余转 `nous-backend`。backend 宿主口退到
  `10.0.0.10:8890`(排障用)。公网 `api.nous.ink` 走 cloudflared→docker 网络,不经网关。
- secret:`secrets/backend.env` 的 `NGINX_SECURE_LINK_SECRET`,backend(签名)与
  gateway(验签)共用同一 env_file。
- DB 开关(base_url 必须是新入口):

  ```sql
  UPDATE public.system_settings
  SET value = '{"enabled": true, "base_url": "https://cn.nous.ink:88",
                "ttl_seconds": 86400}'::json
  WHERE key = 'nginx_direct_serve';
  ```

- 验证:坏签名 `curl 'https://cn.nous.ink:88/f/x?st=a&e=1'` → **403**(打到 FastAPI
  会是 JSON 404,说明网关没接管);cover 端点 302 后 200 且 `Server: nginx`。
- 事故存档(2026-07-27):品牌迁移只改了 env/config.yml,漏了这条 **DB 里的
  base_url**——老域名入口拆除后,302 全部指向死地址,`ERR_EMPTY_RESPONSE` 刷屏。
  DB 存的配置也要进迁移 checklist。

---

# 以下为 NAS 老栈存档(2026-07-06)

> 背景:百万文件 P3(设计:`docs/superpowers/specs/2026-07-06-million-files-single-user-design.md`)。
> 启用后 cover(后续 file)字节由既有 HLS nginx 容器(:8081)sendfile 直发,
> FastAPI 只回 302 签名重定向。**代码合并后默认不生效** —— 需要下面的一次性
> NAS 操作 + DB 开关。任何一步不做,系统保持现状(FileResponse),零风险。

## 一次性 NAS 操作(⚠️ Watchtower 不应用 compose 变更)

1. 生成共享 secret 并写入 host env 文件(backend 与 nginx 共用):

   ```bash
   ssh <nas>   # port 1122, key nas_deploy_key
   openssl rand -hex 32
   # 把输出追加到 /volume1/docker/mediahub/docker/.env:
   # NGINX_SECURE_LINK_SECRET=<上面的值>
   ```

2. 拉新代码后,重建 **仅 nginx** 容器(铁律 `--no-deps`,防漂移栈级联):

   ```bash
   cd /volume1/docker/mediahub
   git pull
   sudo /usr/local/bin/docker compose -f docker/docker-compose.yml up -d --no-deps nginx
   ```

3. backend 容器读取新 env(bind-mount 的 .env,stop/start 即重读):

   ```bash
   sudo /usr/local/bin/docker stop -t 0 mediahub-app-backend && \
   sudo /usr/local/bin/docker start mediahub-app-backend
   # worker 同理:mediahub-worker
   ```

4. 验证 nginx 侧签名闸:

   ```bash
   # 无签名 → 403
   curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8081/f/anything'
   # HLS 原路不受影响 → 200/404 照旧
   curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8081/health'
   ```

## 前置:在 :88 反代加 /f/ 转发(与 /stream/ 同法)

⚠️ 实测(2026-07-06):**8081 从外网不可达** —— 浏览器只到得了 `:88`,那是
群晖系统层 nginx 在做 path 反代(`/api/*`→backend :8880、`/stream/*`→HLS
容器 :8081,后者是当年 HLS 上线时加的规则)。所以 `/f/` 需要同样加一条:

- 群晖 DSM 反向代理(或当年配 /stream/ 的同一处 nginx 规则):
  **无需任何反代改动(2026-07-06 最终形态)**:宿主 8880 = nginx 网关(DSM 规则永不变);backend 宿主端口由 `.env` 的 `APP_PORT=8890` 提供(内网排障口)。网关内部分流:`/f/`、`/stream/` 直发,其余转 backend
- 验证:`curl -s -o /dev/null -w '%{http_code}' 'https://mediahubserver.heygo.cn:88/f/x'`
  应为 **403**(签名闸生效)而非 404/502。

## 打开 DB 开关(Supabase SQL,随时可关)

```sql
INSERT INTO public.system_settings (key, value)
VALUES ('nginx_direct_serve',
        '{"enabled": true,
          "base_url": "https://mediahubserver.heygo.cn:88",
          "ttl_seconds": 86400}'::jsonb)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
```

> base_url = **:88**(经系统反代到 8081),不是 8081 本身 —— 8081 外网不可达。
> 后端配置缓存 60s —— 开关变更 1 分钟内生效。

## 验证(开启后)

```bash
# cover 端点应回 302,Location 指向 /f/...?st=..&e=..
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  'https://mediahubserver.heygo.cn:88/api/v1/resources/<id>/cover'
# 跟随后 200 且 Server: nginx
curl -sIL 'https://mediahubserver.heygo.cn:88/api/v1/resources/<id>/cover' | grep -iE '^(HTTP|server)'
```

前端无需任何改动:`<img>` 自动跟随 302;302 本身带 `private, max-age=600`,
重复渲染 10 分钟内不再触达 gateway。

## 回滚

- 软回滚(秒级):`UPDATE public.system_settings SET value = jsonb_set(value,'{enabled}','false') WHERE key='nginx_direct_serve';` —— cover 立即回到 FileResponse。
- nginx 配置回滚:恢复 compose 里旧的 `nginx-hls.conf` 挂载并 `up -d --no-deps nginx`(模板与旧 conf 的 /stream/、/health 行为一致,一般无需)。

## 已知边界

- 302 方案 gateway 仍承接每次首个 cover 请求(纯重定向,零磁盘 IO);
  彻底绕开 gateway 的"列表响应直带签名 URL"是后续增量,当前收益已 >95%。
- 签名对 `$uri`(percent-decoded)计算 —— 中文/空格文件名已覆盖
  (backend 用原始路径算 md5、URL 里 quote)。
