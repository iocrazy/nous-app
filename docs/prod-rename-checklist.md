# prod 容器重命名维护手册

eng review 2026-04-27 — namespace X (3 分类)

## 命名映射

| Old | New | 数 |
|---|---|---|
| backend `mediahub` | `mediahub-app-backend` | 1 |
| `mediahub-redis` | `mediahub-app-redis` | 1 |
| `mediahub-celery-worker` | `mediahub-app-celery-worker` | 1 |
| `mediahub-celery-beat` | `mediahub-app-celery-beat` | 1 |
| `mediahub-nginx` | `mediahub-app-nginx` | 1 |
| `mediahub-admin` | `mediahub-admin` (**不变**) | 1 |
| supabase `mediahub-{studio/kong/auth/rest/storage/imgproxy/meta/edge-functions/analytics/db/vector/pooler}` | `mediahub-sb-{...}` | 12 |
| `realtime-dev.mediahub-realtime` | `realtime-dev.mediahub-sb-realtime` | 1 |
| **总计** | | **19** |

## 维护窗口预算

- 总耗时 ~10 分钟
- prod 中断 ~5 分钟（supabase 重启 ~3 min + backend 重新部署 ~30 sec + 烟测 ~1 min）
- 选低峰时段（凌晨 / 周末）

## 前置 (pre-flight)

```bash
# 1. PR 已 merge 到 master（仓库改动已生效）
gh pr view <pr-id> --json mergedAt
git log origin/master --oneline | head -5     # 确认 merge commit 在最近

# 2. 确认 NAS 上有 rename 脚本
ssh user@nas -p <port> 'ls -la /volume1/docker/datahub/rename-prod-supabase.sh'

# 3. dry-run 看预期改动
ssh user@nas -p <port> 'sudo bash /volume1/docker/datahub/rename-prod-supabase.sh --dry-run'
```

## 执行 (顺序很重要)

```bash
# === Step 1 — supabase 改名 (NAS-side) ===
ssh user@nas -p <port>
sudo bash /volume1/docker/datahub/rename-prod-supabase.sh --apply
# 等待提示 "Wait 2 min..."

# 等 2 分钟后:
cd /volume1/docker/datahub/sb-mediahub
sudo docker compose ps
# 期望: 13 个 mediahub-sb-* 容器全 healthy / running

# === Step 2 — backend 改名 (从 GitHub Actions 触发) ===
# PR merge 已经触发 deploy-backend.yml,等它跑完
gh run watch --interval 10
# 或手工触发 redeploy:
gh workflow run deploy-backend.yml

# 等 GitHub Actions 完成后:
sudo docker ps --filter name=mediahub-app
# 期望: 5 个 mediahub-app-* 容器（backend/redis/celery-worker/celery-beat/nginx）

# === Step 3 — 烟测 ===
# 用户面 API
curl -sf https://mediahubserver.heygo.cn:88/api/v1/health
# Supabase auth
curl -sf https://sb-mediahub.heygo.cn:88/auth/v1/health
# Frontend (Vercel)
curl -sf https://mediahub.heygo.cn

# === Step 4 — 全栈点检 ===
sudo docker ps --format "table {{.Names}}\t{{.Status}}" | grep mediahub
# 应该看到 (排序后):
# mediahub-admin              Up X (healthy)
# mediahub-app-backend        Up X (healthy)
# mediahub-app-celery-beat    Up X (healthy)
# mediahub-app-celery-worker  Up X (healthy)
# mediahub-app-nginx          Up X
# mediahub-app-redis          Up X (healthy)
# mediahub-sb-analytics       Up X (healthy)
# mediahub-sb-auth            Up X (healthy)
# mediahub-sb-db              Up X (healthy)
# mediahub-sb-edge-functions  Up X (healthy)
# mediahub-sb-imgproxy        Up X (healthy)
# mediahub-sb-kong            Up X
# mediahub-sb-meta            Up X (healthy)
# mediahub-sb-pooler          Up X (healthy)
# mediahub-sb-rest            Up X (healthy)
# mediahub-sb-storage         Up X (healthy)
# mediahub-sb-studio          Up X (healthy)
# mediahub-sb-vector          Up X (healthy)
# realtime-dev.mediahub-sb-realtime  Up X (healthy)
```

## 回滚 (任意阶段失败)

### 回滚 supabase 改名

```bash
ssh user@nas -p <port>
sudo bash /volume1/docker/datahub/rename-prod-supabase.sh --rollback
# 容器名退回 mediahub-* (旧名)
# 数据完整保留 (volumes 没动)
```

### 回滚 backend 改名

```bash
# git 上回滚:
git revert <merge-commit-sha>
git push origin master
# 等 GitHub Actions 重新 deploy 旧版 docker-compose.yml
gh run watch
```

## 已知风险点

1. **supabase down 期间**: 用户登录、media 上传/读取、AI 任务全部失败。Celery 已经在跑的任务会重试或失败。
2. **realtime 客户端重连**: 前端 Supabase Realtime 订阅会断开，自动重连最多 1-2 分钟。
3. **kong cold start**: 第一个请求可能 1-2 秒慢，后续正常。
4. **backend 健康检查 60s start_period**: backend 容器启动后头 1 分钟 GitHub Actions 显示 unhealthy 是正常的,等 healthcheck pass 后转 healthy。

## 相关 PR / commits

- 仓库改名 commits: 见 PR `<待开>`，主要影响:
  - `docker/docker-compose.yml`
  - `.github/workflows/run-migration.yml`
  - `docker/README.md`
  - `scripts/sync-prod-to-dev.sh` (`PROD_DB`)
  - `scripts/clone-supabase-to-dev.sh` (`mediahub-sb-db`)
- NAS 端脚本: `/volume1/docker/datahub/rename-prod-supabase.sh`
