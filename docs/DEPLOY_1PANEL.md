# 1Panel 部署指南

本文档介绍如何在 1Panel 中部署 MediaHub。

## 前提条件

- 1Panel 已安装并运行
- Docker 已启用
- NAS 有足够的存储空间

---

## 方案 A：使用 Docker Compose（推荐）

### 1. 上传代码到 NAS

```bash
# SSH 登录 NAS
ssh root@your-nas-ip

# 创建项目目录
mkdir -p /opt/mediahub
cd /opt/mediahub

# 克隆代码（或上传）
git clone https://github.com/your-repo/mediahub.git .
```

### 2. 配置环境变量

```bash
# 复制示例配置
cp backend/.env.example backend/.env

# 编辑配置
nano backend/.env
```

填写以下内容：

```bash
# Supabase 配置
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# 下载路径（容器内路径）
NAS_BASE_PATH=/app/downloads

# 服务器配置
HOST=0.0.0.0
APP_PORT=8080
```

### 3. 在 1Panel 中部署

#### 方式 1：1Panel Compose 功能

1. 登录 1Panel 后台
2. 进入 **容器** → **Compose**
3. 点击 **创建 Compose**
4. 填写信息：
   - 名称：`mediahub`
   - 路径：`/opt/mediahub`
5. 点击 **部署**

#### 方式 2：命令行部署

```bash
cd /opt/mediahub
docker-compose up -d --build
```

### 4. 配置反向代理（可选）

在 1Panel 中配置 Nginx 反向代理：

1. 进入 **网站** → **创建网站**
2. 选择 **反向代理**
3. 填写：
   - 域名：`mediahub.your-domain.com`
   - 代理地址：`http://127.0.0.1:8080`
4. 配置 SSL 证书（推荐）

---

## 方案 B：1Panel 运行环境（需要自定义镜像）

由于 MediaHub 需要 Chrome 浏览器，不能直接使用 1Panel 的 Python 运行环境。

### 1. 先构建自定义镜像

```bash
cd /opt/mediahub
docker build -t mediahub:latest .
```

### 2. 在 1Panel 创建容器

1. 进入 **容器** → **创建容器**
2. 填写配置：

| 配置项 | 值 |
|--------|-----|
| 镜像 | `mediahub:latest` |
| 容器名称 | `mediahub` |
| 端口映射 | `8080:8080` |
| 目录映射 | `/your/nas/downloads:/app/downloads` |
| 环境变量 | 见下方 |

3. 环境变量：
   ```
   TZ=Asia/Shanghai
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_ANON_KEY=your-anon-key
   SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
   NAS_BASE_PATH=/app/downloads
   ```

---

## 目录映射说明

| 容器内路径 | NAS 路径（示例） | 说明 |
|-----------|-----------------|------|
| `/app/downloads` | `/volume1/docker/mediahub/downloads` | 视频下载目录 |
| `/app/.env` | `/volume1/docker/mediahub/.env` | 环境变量 |
| `/app/frontend_config.yml` | `/volume1/docker/mediahub/frontend_config.yml` | 前端配置 |

---

## 验证部署

### 1. 检查容器状态

```bash
docker ps | grep mediahub
```

### 2. 查看日志

```bash
docker logs -f mediahub
```

### 3. 访问服务

- API 文档：`http://your-nas-ip:8080/docs`
- 健康检查：`http://your-nas-ip:8080/health`

---

## 前端部署

前端是静态文件，可以单独部署：

### 方式 1：构建后部署到 Nginx

```bash
cd frontend
npm install
npm run build

# 将 dist 目录部署到 1Panel 的网站目录
cp -r dist/* /path/to/nginx/html/
```

### 方式 2：使用 1Panel 创建静态网站

1. 进入 **网站** → **创建网站**
2. 选择 **静态网站**
3. 上传构建后的 `dist` 目录

### 前端环境变量

创建 `frontend/.env.production`：

```bash
VITE_API_URL=http://your-nas-ip:8080
# 如果不在 YAML 配置中设置，需要这里配置
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
```

---

## 常见问题

### Q: Chrome 启动失败？

检查容器是否有足够内存（建议 2GB+）：

```bash
docker stats mediahub
```

### Q: 视频下载到哪里？

默认下载到容器内 `/app/downloads`，通过 volume 映射到 NAS 的指定目录。

### Q: 如何更新？

```bash
cd /opt/mediahub
git pull
docker-compose up -d --build
```

### Q: 如何查看实时日志？

```bash
docker logs -f mediahub
```

或在 1Panel 中：**容器** → **mediahub** → **日志**

---

## 资源占用参考

| 状态 | CPU | 内存 |
|------|-----|------|
| 空闲 | <1% | ~300MB |
| 解析视频 | 10-30% | ~800MB-1.5GB |
| 下载视频 | 5-10% | ~400MB |

建议为容器分配 **2GB 内存限制**。
