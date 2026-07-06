# ADR: 主媒体库暂不迁对象存储 —— 文件系统 + nginx 直出是当前最优

> Status: **ACCEPTED**(2026-07-06,用户与 Claude 架构评审后记录)
> 复议触发条件见文末 —— 满足任一条件时重开本决策。

## 背景

- 存储现状是**双轨制**:聊天/AI 生成小图 → Supabase Storage(真·对象存储,S3 兼容);
  主媒体库(下载/上传素材,规划至百万文件)→ NAS 文件系统 + DB `file_path` 寻址 + nginx sendfile 直出。
- 百万文件 readiness 四 Phase(#1078/#1084/#1085/#1086)已按文件系统路线收官,
  其中 nginx `secure_link` 签名直出 = 对象存储 presigned URL 模式的零迁移等价物。
- 问题:主库是否应(现在或不计成本地)迁对象存储,以获得"提速与扩展"?

## 决策:不迁。即使迁移成本为零也不迁(单机阶段)。

**本质论**:对象存储不是"更快的存储",而是**用性能换分布式语义**的抽象
(扁平 key 空间 + HTTP 原子 PUT + 无 POSIX 包袱 → 换水平扩展能力)。
本系统负载 = 单写入方、重读、大文件流式、本地 ffmpeg 重处理 ——
每一条都站在 POSIX 文件系统的主场。

### 收益项逐条被现有组合覆盖

| 对象存储卖点 | 现有等价物 |
|---|---|
| presigned URL 访问控制 | nginx `secure_link` 签名直出(#1086) |
| 版本化 | `resource_versions` 表 |
| 元数据附着 | DB(`resources` 行)是权威 |
| key 寻址、免路径耦合 | `file_path` 列即 key,读端从不猜路径 |
| 生命周期/冷热分层 | 单机单卷无层可分 |

### 损失项是实打实的

1. **sendfile 零拷贝没了** —— 一切字节过应用进程(storage-api=Node;MinIO 稍好
   但同为进程+HTTP)。单机吞吐恒为 `nginx sendfile > MinIO > storage-api`。
   同机自托管下,"改对象存储"= 把刚从 FastAPI 手里拿走的搬字节活换个进程雇回来。
2. **同卷 rename O(1) 没了** —— 对象"移动"= copy+delete;sideload(#1084)
   靠 rename 才做到百万文件 ≈4 小时,对象存储下退化为百万次全量复制。
3. **工具链直读没了** —— ffmpeg/HLS/缩略图现在直接吃路径;对象存储下每个
   worker 任务多两趟全量 IO(拉临时文件 + 传回)。
4. **扩展是假的**(自托管同机):Supabase Storage/MinIO 的 backend 仍是同一块卷,
   换接口不换地基 —— 容量、吞吐、单点故障全都没变。

### 大厂对照

原片在 S3,但**分发的最后一跳永远是文件化的 CDN 边缘节点**(本质=一排 nginx
sendfile)。本系统只有一跳,所以直接"住在边缘节点上"即最优。

## 复议触发条件(满足任一即重开)

1. **第二个计算节点开始写文件**(不只是读)—— NFS 写锁语义是噩梦,对象存储的
   原子 PUT 干净得多。此时选 **MinIO(真分布式)**,不选 storage-api。
2. **需要跨机冗余** —— MinIO ≥4 节点擦除编码(单 NAS 上此职责属 RAID/btrfs)。
3. **云冷热分层 / 公网 CDN 分发** —— 云对象存储(OSS/S3)+ CDN,可增量迁
   (新文件双写、旧文件懒迁),买的是"别人的机房",红利真实。

## 关联演进(已另行讨论,非本 ADR 范围)

NAS 退居纯存储、服务迁 Threadripper+RTX 6000 Pro 时:计算存储分离用 **NFS**
即可(单一消费方);10GbE 是硬前提;**Postgres 随计算走(本地 NVMe,绝不
放 NFS)**;GPU 承接 NVENC 转码 + 本地 LLM/embedding。届时整套 compose +
`DOWNLOAD_PATH` 相对路径寻址使迁移成本天然低。
