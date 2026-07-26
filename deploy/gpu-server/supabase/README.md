# 自托管 Supabase 栈（gpupc）

## 为什么它现在在 git 里

**在此之前它从未被版本控制过** —— nas-A 时代靠 Portainer 手工维护，迁到 gpupc 后靠机器上一份裸文件。后果在 2026-07-23 兑现：

P1 迁移时这份 compose 是照 Supabase 官方模板**重新搭**的，而不是移植 nas-A 的旧配置。于是 storage 的落盘从「群晖本地卷」变成了「CIFS 网络挂载」——**一个足以让所有上传挂掉的改动，没有任何 diff、review 或记录**。file 后端用 xattr 存对象元数据，CIFS 不支持，上传全线 502，静默 3 天才被发现（读一直正常，验收只测了读）。

纳入 git 就是为了让这类改动**必须留痕**。

## 真相在哪台机器上

⚠️ **运行时的真相仍在 gpupc 的 `/media/heygo/program/datahub/nous/supabase/`，不在这里。**

这个目录是**副本 + 版本记录**，不是部署源。改配置的正确顺序：

```bash
# 1) 改机器上的真文件（这才是生效的那份）
vim /media/heygo/program/datahub/nous/supabase/docker-compose.yml
docker compose up -d <service>

# 2) 验证生效后，把改动同步回 git 并提交
cp /media/heygo/program/datahub/nous/supabase/docker-compose.yml \
   deploy/gpu-server/supabase/docker-compose.yml
git add -p && git commit
```

反过来也一样：从 git 拉到新改动后，要手工 `cp` 回机器目录才生效 —— **没有 CI 会替你做这件事**。

> 未来可以让 `deploy-gpu.yml` 直接从 repo 部署这份 compose，彻底消除双份。在那之前，同步靠人。

## 密钥边界

| 文件 | 进 git | 内容 |
|------|--------|------|
| `docker-compose.yml` | ✅ | 服务定义、端口、挂载、`STORAGE_BACKEND` 等**结构性配置** |
| `.env.example` | ✅ | 变量名清单 + 占位值 |
| `volumes/**` | ✅ | Kong 路由、DB 初始化 SQL、pooler 配置 |
| **`.env`** | ❌ **永不** | 真实密钥，住 `/media/heygo/program/datahub/nous/secrets/supabase.env` |

compose 里 154 处密钥全部走 `${VAR}` 引用，本身不含明文。加新变量时保持这个形式。

## 当前存储架构（2026-07-25 起）

```
storage-api (STORAGE_BACKEND=s3) ──S3/HTTP──> nas-B:8333 SeaweedFS ──> nas-B 本地盘
```

不再有 CIFS bind mount。设计与迁移过程见
[`docs/superpowers/specs/2026-07-25-storage-s3-seaweedfs-migration-design.md`](../../../docs/superpowers/specs/2026-07-25-storage-s3-seaweedfs-migration-design.md)。

## 改这里之后必做的验收

存储/挂载/容器 user 相关的改动，**读探针一律测不出问题**。照 `CLAUDE.md`「部署验收纪律」跑写入冒烟：

```bash
# 逐容器写入探针
docker exec nous-storage sh -c "touch /tmp/.w && rm /tmp/.w"

# 端到端：真实上传一次，别只测下载
```
