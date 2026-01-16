# 群晖 NAS 部署指南

## 前提条件

- 群晖 DSM 7.0+
- Container Manager（Docker）已安装
- SSH 已启用

---

## 部署步骤

### 1. SSH 登录群晖

```bash
ssh admin@your-nas-ip
sudo -i  # 切换到 root
```

### 2. 创建项目目录

```bash
# 在 docker 共享文件夹中创建目录
mkdir -p /volume1/docker/mediahub
cd /volume1/docker/mediahub

# 克隆代码
git clone https://github.com/your-repo/mediahub.git .

# 或者通过 File Station 上传代码
```

### 3. 配置环境变量

```bash
cp backend/.env.example backend/.env
nano backend/.env
```

填写：

```bash
# Supabase 配置
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# 下载路径（容器内）
NAS_BASE_PATH=/app/downloads

# 服务配置
HOST=0.0.0.0
APP_PORT=8080
```

### 4. 创建下载目录

```bash
mkdir -p downloads
chmod 777 downloads
```

### 5. 构建并启动

```bash
# 构建镜像（首次需要几分钟）
docker-compose build

# 启动服务
docker-compose up -d
```

### 6. 检查运行状态

```bash
# 查看容器状态
docker ps

# 查看日志
docker logs -f mediahub-backend
```

---

## 群晖 Container Manager GUI 部署（可选）

如果不想用命令行，可以用图形界面：

### 1. 构建镜像

先在命令行构建：
```bash
cd /volume1/docker/mediahub
docker build -t mediahub:latest .
```

### 2. 在 Container Manager 创建容器

1. 打开 **Container Manager** → **容器** → **新增**
2. 选择镜像 `mediahub:latest`
3. 配置：

| 设置 | 值 |
|------|-----|
| 容器名称 | `mediahub-backend` |
| 自动重启 | ✅ 启用 |
| 端口 | 本地 `8080` → 容器 `8080` |

4. 存储空间映射：

| 本地路径 | 容器路径 | 说明 |
|---------|---------|------|
| `/volume1/docker/mediahub/downloads` | `/app/downloads` | 下载目录 |
| `/volume1/docker/mediahub/backend/.env` | `/app/.env` | 环境变量 |

5. 环境变量：
   - `TZ` = `Asia/Shanghai`

---

## 访问服务

| 服务 | 地址 |
|------|------|
| 后端 API | `http://NAS-IP:8080` |
| API 文档 | `http://NAS-IP:8080/docs` |
| 前端 | `http://NAS-IP:3000` |

---

## 前端部署

### 方式 A：使用 docker-compose（已包含）

前端会自动部署到 3000 端口，但需要先构建：

```bash
cd /volume1/docker/mediahub/frontend
npm install
npm run build
```

然后启动：
```bash
cd /volume1/docker/mediahub
docker-compose up -d mediahub-frontend
```

### 方式 B：使用 Web Station

1. 构建前端：
   ```bash
   cd frontend
   npm install
   npm run build
   ```

2. 在群晖 **Web Station** 中创建虚拟主机
3. 将 `frontend/dist` 目录设为网站根目录

---

## 更新部署

```bash
cd /volume1/docker/mediahub

# 拉取最新代码
git pull

# 重新构建并启动
docker-compose down
docker-compose build
docker-compose up -d
```

---

## 常见问题

### Q: 构建失败，提示内存不足？

群晖 Docker 默认可能限制了内存，尝试：
```bash
docker build --memory=2g -t mediahub:latest .
```

### Q: Chrome 启动失败？

检查容器日志：
```bash
docker logs mediahub-backend
```

常见原因：
- 内存不足（建议 2GB+）
- 缺少共享内存，添加 `--shm-size=1g` 参数

### Q: 下载的视频在哪里？

在 NAS 上的 `/volume1/docker/mediahub/downloads` 目录。

### Q: 如何查看实时日志？

```bash
docker logs -f mediahub-backend
```

或在 Container Manager GUI 中查看。

---

## 资源监控

在 Container Manager 中可以看到 CPU 和内存使用情况。

预期资源占用：
- 空闲：~300MB 内存
- 解析视频：~800MB-1.5GB 内存
- CPU：解析时 10-30%，空闲时 <1%
