# Prod 迁移到 PG 17 — NAS 操作清单

> 给执行人的 copy-paste 手册。每一步都标注了"在哪儿跑、看什么输出、出错怎么办"。
>
> 详细原理见 `prod-migration-pg17.md`。

---

## 阶段编号速查

| Stage | 动作 | 对业务影响 | 预计耗时 |
|---|---|---|---|
| **S1** | 部署新 prod PG17 容器（并行） | 无 | 15 分钟 |
| **S2** | 演练：老 prod → dev 灌数据 | 无 | 30 分钟 |
| **S3** | 真迁：维护窗口 + 老 prod → 新 prod | **停服 ~30 分钟** | 30-45 分钟 |
| **S4** | 切流量 + 撤维护页 | **完全恢复** | 5 分钟 |
| **S5** | 老 prod 保留观察 7 天，到期清理 | 无 | — |

---

## 前提

- SSH 到 NAS：`ssh <user>@<nas-ip> -p <NAS_PORT>`
- 项目 worktree 已 `git pull` 到最新（脚本最新版包含 `--target` 参数）
- 老 prod 还在跑（`mediahub-*` 容器在 9080 / 55432）
- dev 已就绪并健康（`mediahub-sb-dev-*` 容器在 9081 / 55433，13 个全 healthy）

---

## S1 — 部署新 prod 容器（无影响，随时跑）

```bash
# SSH 到 NAS
ssh <user>@<nas-ip> -p <NAS_PORT>

# 拷脚本（假设 mediahub 仓库已经在 NAS 上 pull）
cd /volume1/docker/mediahub
git pull origin master   # 或者你 worktree 的对应分支

# 跑 setup
sudo bash scripts/setup-prod-pg17.sh
```

**期待输出**：

```
>> Copying compose template + utils + volumes from dev
>> Renaming compose for prod
>> Generating .env (preserving JWT_SECRET / ANON_KEY / SERVICE_ROLE_KEY ...)
>> Generating asymmetric keys (sb_publishable / sb_secret / JWT_KEYS / JWT_JWKS)
>> DONE.
Setup complete: /volume1/docker/datahub/mediahub-sb-prod
```

启动 13 个容器：

```bash
cd /volume1/docker/datahub/mediahub-sb-prod
sudo docker compose up -d
# 等 5 分钟（PG 17 init + supabase 子服务启动）

sudo docker compose ps
# 期待: 13 个容器全部 "healthy" 或 "running"
```

**13 个容器名**：
```
mediahub-sb-prod-{db,kong,auth,rest,storage,realtime,
                  imgproxy,meta,edge-functions,analytics,
                  vector,pooler,studio}
```

**出问题怎么办**：
- 某个容器 unhealthy → `sudo docker compose logs --tail=100 <service>`
- db 起不来 → 检查 `/volume1/docker/datahub/mediahub-sb-prod/volumes/db/data` 是否为空（应该为空，如果有残留 → `sudo docker compose down -v && sudo rm -rf volumes/db/data && sudo docker compose up -d`）
- analytics 一直 starting → 多等 2 分钟，logflare 第一次启动慢

---

## S2 — 演练：老 prod 数据 → dev（验证 migrate 脚本）

⚠️ **会覆盖 dev 现有数据**。继续之前确认 dev 的 DBOS PoC 测试数据可丢。

```bash
cd /volume1/docker/mediahub  # 仓库根
sudo bash scripts/migrate-prod-data.sh --check --target=dev
```

**期待输出**：

```
>> Mode: --check   Target: dev (mediahub-sb-dev-db)
>> Pre-migration counts (sanity check)
OLD prod (mediahub-db):
ai_agents|N
auth.users|N
parsed_media|N
projects|N
...
TARGET dev (mediahub-sb-dev-db) — should be near-empty (just init):
ai_agents|0 (or NULL/error if table doesn't exist yet)
...
```

记录老 prod 各表行数 → 待会儿验证。

```bash
sudo bash scripts/migrate-prod-data.sh --apply --target=dev
```

**期待输出**：

```
>> Mode: --apply   Target: dev (mediahub-sb-dev-db)
>> [1/4] Dumping schema-only from old prod (NO data)
   schema dump: NNNN lines
>> [2/4] Dumping data-only from old prod (skip log tables to save space)
   data dump: NNNN lines, NNN MB
>> [3/4] Restoring schema to new prod
   schema apply log: /tmp/mh-migration/schema-apply.log
   errors: N
>> [4/4] Restoring data to new prod (this is the slow step)
   data apply log: /tmp/mh-migration/data-apply.log
   errors: N
>> Restore complete. Run with --verify to compare row counts.
```

验证：

```bash
sudo bash scripts/migrate-prod-data.sh --verify --target=dev
```

输出会并排显示老 prod / dev 的行数。**所有非日志表行数应该完全一致**，否则诊断 `/tmp/mh-migration/data-apply.log` 中的 ERROR 行。

**演练成功标志**：
- ✅ schema-apply.log errors 数 < 100（基本是"already exists"，无害）
- ✅ data-apply.log errors 数 < 50（基本是 auth.users 等内置 row 冲突，无害）
- ✅ verify 中 parsed_media / resources / unified_tasks / projects 行数完全一致
- ✅ dev frontend (http://192.168.50.9:9081) 能登录并看到老 prod 的数据

---

## S3 — 真迁（维护窗口）

⚠️ **业务停服**。建议时间：**周末晚上 22:00-02:00**。

### S3.1 — 业务进维护模式

```bash
# 把前端 Vercel 切到 maintenance build，或 nginx 反代返回 503
# （具体步骤取决于你的维护页设置）
```

确认前端用户看到"系统维护中"页面，后端无新写入。

### S3.2 — 跑迁移

```bash
cd /volume1/docker/mediahub
sudo bash scripts/migrate-prod-data.sh --check --target=prod
# 检查老 prod 行数；新 prod 应该是 init 后的空状态
```

```bash
sudo bash scripts/migrate-prod-data.sh --apply --target=prod
# 会有 5 秒倒计时警告（这是 prod 真迁），Ctrl-C 中止
# 30-45 分钟
```

```bash
sudo bash scripts/migrate-prod-data.sh --verify --target=prod
# 行数对比，必须吻合（除日志表）
```

### S3.3 — Smoke test（在新 prod 上）

```bash
# 直连新 prod 的 Studio 看一眼
# http://192.168.50.9:3082 → DASHBOARD_USERNAME=heygo / DASHBOARD_PASSWORD（从 .env 读）

# 或者直接 SQL 抽查关键表
sudo docker exec mediahub-sb-prod-db psql -U postgres -d postgres -c "
SELECT 'parsed_media', COUNT(*) FROM parsed_media
UNION ALL SELECT 'resources', COUNT(*) FROM resources
UNION ALL SELECT 'auth.users', COUNT(*) FROM auth.users;"
```

---

## S4 — 切流量

### S4.1 — 改后端 .env

```bash
ssh <user>@<nas-ip> -p <NAS_PORT>
cd /volume1/docker/mediahub

# 编辑 backend .env：把 SUPABASE_URL / KEYS 指向新 prod
# SUPABASE_URL=http://kong:8000  # 内部走 docker network 还是 9082，看你的 nginx 配置
# 或外部: SUPABASE_URL=https://mediahub-sb-prod.heygo.cn:88
# SUPABASE_ANON_KEY=<新 prod 的 SUPABASE_PUBLISHABLE_KEY>
# SUPABASE_SERVICE_ROLE_KEY=<新 prod 的 SUPABASE_SECRET_KEY>

cd docker
sudo docker compose up -d --force-recreate backend celery-worker
sudo docker compose logs --tail=50 backend
# 期待: 启动正常，能连上新 prod
```

### S4.2 — 改前端 Vercel

在 Vercel Dashboard 改环境变量：

```
VITE_SUPABASE_URL = https://mediahub-sb-prod.heygo.cn:88
VITE_SUPABASE_ANON_KEY = <新 prod publishable key>
```

→ Redeploy → 撤维护页。

### S4.3 — 验证

```
- 浏览器登录 https://mediahub.heygo.cn
- 解析一条视频 → 应该走新链路
- 进资源库 → 看到迁移过来的数据
- F12 看请求 → SUPABASE_URL 是新 prod
```

---

## S5 — 观察期（7 天）

老 prod (`sb-mediahub`) **保留容器和卷**，万一回滚直接改回老 .env：

```bash
# 老 prod 已经被 stop（停止接受写）但卷还在
cd /volume1/docker/datahub/sb-mediahub
sudo docker compose ps    # 确认全部 Exit 或 down
sudo docker compose down  # 注意：不带 -v！保留卷以便回滚
```

7 天平稳 → 可以拆：

```bash
cd /volume1/docker/datahub/sb-mediahub
sudo docker compose down -v   # ⚠️ 这一步不可逆，会删数据卷
```

---

## 回滚（万一新 prod 出问题）

**条件**：S4.2 撤维护页之后才有用户写入新 prod。如果撤维护页 < 5 分钟内就发现问题：

1. 立刻把前端切回维护页
2. 改后端 .env 回老 prod 的 URL/keys
3. `sudo docker compose up -d --force-recreate backend celery-worker`
4. 改 Vercel env 回老 prod URL → redeploy → 撤维护页

**新 prod 的 5 分钟新写入会丢**（不可避免，trade-off）。

如果撤维护页之后过了一段时间发现问题，回滚就更复杂——需要把新 prod 这段时间的数据 dump 出来 merge 回老 prod。这种情况发生的概率极低，但如果真出现，先停服再讨论。

---

## 我（Claude）能远程协助的部分

- 看 `docker compose logs <service>` 输出 → 帮你定位问题
- 写诊断 SQL → 你贴回输出 → 我解读
- 在迁移过程中 narrate 期待的输出，你确认是否吻合

**SSH 我连不上**（你之前说 NAS_PORT 是 secret），所有 docker 命令都要你在 NAS 上跑。
