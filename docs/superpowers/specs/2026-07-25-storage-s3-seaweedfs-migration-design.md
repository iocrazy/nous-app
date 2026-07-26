# Supabase Storage：file 后端 → S3 后端（SeaweedFS on nas-B）迁移设计

日期：2026-07-25
状态：已批准（用户拍板：SeaweedFS / 数据目录 Sources/nous 下 / SSH 直连操作）

## 1. 背景与根因

P1 迁移（NAS→gpupc）后，`nous-storage` 的落盘从**群晖本地卷**变成了 **CIFS 网络挂载**
（`/mnt/heytime/Sources/nous/object-storage` → `//192.168.8.9/Sources`）。
storage-api 的 file 后端用 **xattr** 存对象元数据（`file.js` 硬依赖 `fs-xattr`，无降级路径），
而 CIFS 不支持 setxattr（errno 95）→ **2026-07-23 起所有上传 502**，
`storage.objects` 最后一次成功写入停在 07-22。读取不受影响（字节流不走 xattr）。

事实澄清（2026-07-25 实measured）：老 nas-A 栈**也是 file 后端**（非 MinIO/S3），
它能工作纯粹因为容器与磁盘同机、本地 ext4/btrfs xattr 可用。
真正的回归 = 本地盘 → 网络文件系统，不是后端类型变化。

## 2. 决策记录

| 决策 | 结论 | 依据 |
|---|---|---|
| 修复方向 | 切 `STORAGE_BACKEND=s3`，弃 file 后端 | 根除 xattr 依赖；S3 API 是对象存储事实标准；未来换存储只改 endpoint |
| 引擎 | **SeaweedFS**（放弃 MinIO） | MinIO 社区版 2026-04 已归档停维护（2025-10 起无 Docker 镜像）；SeaweedFS Apache 2.0、12 年开发、~30K stars、海量小文件性能强、加 volume server 原生扩容 |
| 引擎备选 | Garage（AGPL） | 兼容性闸门不过时启用；此时数据未动，零成本换向 |
| 部署位置 | **nas-B 上跑 SeaweedFS 容器**（Portainer/Docker 已在） | 存储引擎必须贴着磁盘跑；跨机器走 S3/HTTP 协议而非网络文件系统 —— 这正是本次事故的教训 |
| 数据目录 | nas-B `Sources/nous/seaweedfs-data`（本地 bind，**不经 CIFS**） | 用户要求数据留 nas-B 数据盘。注意：SeaweedFS 内部格式，File Station 不可按文件浏览 |
| MinIO on gpupc + CIFS | **否决** | 官方不支持网络文件系统；等于换一种姿势再踩同一坑 |
| gpupc 本地盘 | **否决**（用户明确禁止） | 数据必须在 nas-B |

## 3. 目标架构

```
gpupc                                      nas-B (192.168.8.9)
┌──────────────────────────┐   S3/HTTP     ┌───────────────────────────────┐
│ nous-storage             │─────────────> │ SeaweedFS 单容器               │
│  STORAGE_BACKEND=s3      │  LAN 0.6ms    │  weed server -s3 (:8333 对外)  │
│ nous-imgproxy            │─────────────> │  data: Sources/nous/           │
│  (600s 预签名URL直取,实证) │              │        seaweedfs-data (本地bind)│
└──────────────────────────┘               └───────────────────────────────┘
```

- 单容器 `weed server -s3`（master/volume/filer/s3 同进程），只对外发布 **:8333**
  （8888 filer / 9333 master 不暴露，已实测确认）
- 镜像固定版本 tag（不用 latest）
- S3 身份：专用 access/secret key
  - ⚠️ **待收紧**：用 `AWS_ACCESS_KEY_ID/SECRET` 环境变量启动时，SeaweedFS 授予的是
    **admin 级权限**（实测该身份可创建/删除任意 bucket）。应改为挂载 `s3.json`
    精确授权，仅 `Read/Write/List/Tagging:nous`：
    ```json
    {"identities":[{"name":"nous-storage",
      "credentials":[{"accessKey":"...","secretKey":"..."}],
      "actions":["Read:nous","Write:nous","List:nous","Tagging:nous"]}]}
    ```
    风险等级：中低（8333 不对公网，密钥仅 storage-api 持有），但应在收尾阶段完成
- imgproxy 无需改挂载：S3 后端下 storage-api 给它 600s 预签名 HTTP URL
  （已从 `s3/adapter.js` 的 `privateAssetUrl` 实证），只需网络可达 nas-B:8333

## 4. gpupc 侧配置变更（supabase compose env）

```
STORAGE_BACKEND=s3
STORAGE_S3_ENDPOINT=http://192.168.8.9:8333
STORAGE_S3_FORCE_PATH_STYLE=true
STORAGE_S3_REGION=us-east-1
STORAGE_S3_DISABLE_CHECKSUM=true        # 新版 AWS SDK 校验和对第三方 S3 的已知坑
AWS_ACCESS_KEY_ID=<seaweedfs key>
AWS_SECRET_ACCESS_KEY=<seaweedfs secret>
# GLOBAL_S3_BUCKET=nous / TENANT_ID=app 不变
```

CIFS 卷挂载从 storage/imgproxy 服务移除（防误写；文件树保留原地作回滚保底）。

## 5. 数据映射与迁移（18G / 747 磁盘文件 / 730 DB 对象）

磁盘树即 S3 key 镜像：`object-storage/nous/app/<bucket>/<key>` → S3 bucket `nous`, key `app/<bucket>/<key>`。

- **以 storage.objects（DB）为真相源**：逐对象定位 CIFS 文件 → PUT 到 SeaweedFS，
  Content-Type 取 DB `metadata->>mimetype`（磁盘 xattr 早已丢失，按扩展名猜不如 DB 准）
- 执行前先做**键位映射实证**：抽 10 条 DB 对象反推磁盘路径逐一命中（含 version 列语义确认），
  防止 file 后端 version 子目录规则与假设不符
- 磁盘孤儿文件（约 17 个，含 .DS_Store）不迁，出清单人工过目
- 千兆局域网 18G ≈ 3-5 分钟；上传功能本就中断，无新增增量

## 6. 兼容性闸门（迁数据之前，不过则换 Garage）—— ✅ 7/7 全过（2026-07-25 实测）

对 nas-B `192.168.8.9:8333` 实测 storage-api 全部依赖操作：
- [x] 认证：匿名 403 / 错密钥 403 / 正确密钥 200
- [x] create bucket `nous`
- [x] PUT 对象（带 Content-Type）
- [x] GET 回读 md5 一致 + Content-Type 保留
- [x] List objects
- [x] **AWS SDK v3 客户端**（storage-api 实际使用的路径）
- [x] **Multipart 20MB**（大文件路径，md5 一致）
- [x] **预签名 URL**（imgproxy 取图路径，md5 一致）

结论：SeaweedFS 4.40 与 storage-api 全兼容，备选方案 Garage 未启用。
闸门脚本保留在 scratchpad，**每次更换 S3 引擎都应重跑**（各家 S3 实现
在 checksum / multipart / ACL 语义上有细微差异）。

### 6.1 部署踩坑记录：群晖 ACL 导致容器启动死循环

现象：容器反复「启动中 → 意外停止」。日志：
```
Folder /data Permission: ----------
Check Meta Folder (-mdir="/data") Writable: Not writable!
```
根因：群晖共享文件夹用 **ACL** 管权限，POSIX 权限位留空（`d---------+`）。
而 SeaweedFS 启动检查是 `0200 & perm`——**直接读属主写位，不是真尝试写入**，
所以即便容器以 root 运行（实际可写），检查也失败 → 退出 → restart 拉起 → 死循环。

修复：`chmod 755 /volume2/Sources/nous/s3`。
**新建数据目录后必须确认 POSIX 位**，这是群晖上跑容器的通用注意事项。

## 7. 切换步骤与验收

1. 备份 supabase compose + storage env（改前快照）
2. 改 env（§4）→ `docker compose up -d storage imgproxy`
3. 验收（**全部通过才算完**）：
   - [ ] 旧对象读取：每 bucket 抽 2 个走 storage-api GET，md5 与 CIFS 原文件一致
   - [ ] imgproxy 变换：实取一张图的 resize
   - [ ] **真实上传冒烟**：API 上传 → 200 → 下载回读一致（上次迁移正是缺这条验收）
   - [ ] Playwright E2E：复跑 probe-upload.mjs，灵感笔记传图 → 图片正常显示
   - [ ] `docker logs nous-storage` 无 5xx / xattr 错误

## 8. 回滚

- CIFS 旧文件树**只读保留 ≥2 周**
- 回滚 = env 改回 `STORAGE_BACKEND=file` + 恢复卷挂载 + 重启，约 1 分钟
- 迁移期间产生的新上传会随回滚丢失（当前上传本就中断，窗口内增量预期为 0）

## 9. 运维

- **备份**：nas-B Hyper Backup 纳入 `Sources/nous/seaweedfs-data`（恢复以整目录为单位，内部格式不可按文件挑拣）
- **版本**：镜像 tag 固定；升级 = 手动改 tag + 看 release notes
- **密钥**：S3 key 存 gpupc secrets 目录（不进 git）；本设计文档不含密钥明文
- **监控**：上传冒烟纳入 /canary 或定期检查（本次事故的直接教训）

## 10. 附录：未来迁移 runbook（应用侧永远只改 endpoint）

| 场景 | 操作 |
|---|---|
| 换 NAS（nas-B→nas-C） | 新节点起 SeaweedFS → `mc mirror` / `rclone sync` 追平 → 改 `STORAGE_S3_ENDPOINT` 重启 storage |
| 上云（OSS/COS/R2） | `rclone sync seaweed: oss:` → 改 endpoint + 密钥 → 重启。全程 S3 API，应用零改动 |
| 多 NAS 扩容 | SeaweedFS 加 volume server 节点（原生支持增量扩容），endpoint 不变 |
| 换引擎（如 Garage） | 同"换 NAS"：新引擎起 → mirror → 切 endpoint |

## 11. 已知风险

- SeaweedFS 与 storage-api 配对的公开案例少于当年 MinIO → 靠 §6 闸门兜底
- 单节点无冗余：依赖 nas-B RAID + Hyper Backup；真分布式等多 NAS 时再扩
- DSM 大版本升级对 Docker 容器的影响：升级前照常快照
