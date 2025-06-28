# Docker 挂载配置详解

本文档详细说明了项目中Docker的挂载配置，包括开发环境和生产环境的不同挂载策略。

## 🔗 挂载配置概览

### 开发环境 (docker-compose.yml)

```yaml
services:
  douyin-app:
    volumes:
      # 🔗 数据挂载配置 - 使用Docker volumes（开发环境）
      # 数据库文件持久化
      - douyin_data:/app/data
      # 下载视频文件持久化
      - douyin_videos:/app/videos
      # 应用日志持久化
      - douyin_logs:/app/logs
      # 配置文件挂载（开发环境可选）
      - ./config.yml:/app/config.yml:ro

  redis:
    volumes:
      # 🔗 Redis数据持久化
      - redis_data:/data

# Docker volumes定义
volumes:
  douyin_data:
    driver: local
  douyin_videos:
    driver: local
  douyin_logs:
    driver: local
  redis_data:
    driver: local
```

### 生产环境 (docker-compose.prod.yml)

```yaml
services:
  douyin-app:
    volumes:
      # 🔗 数据挂载配置 - 宿主机路径:容器路径
      # 数据库文件挂载
      - /var/douyin/data:/app/data
      # 下载视频文件挂载  
      - /var/douyin/videos:/app/videos
      # 应用日志挂载
      - /var/douyin/logs:/app/logs
      # 配置文件挂载（只读）
      - ./config.yml:/app/config.yml:ro

  redis:
    volumes:
      # 🔗 Redis数据挂载
      - /var/douyin/redis:/data

  nginx:
    volumes:
      # 🔗 Nginx配置文件挂载
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      # SSL证书挂载
      - /etc/letsencrypt:/etc/letsencrypt:ro
      # 静态文件挂载（如果有）
      - ./static:/var/www/static:ro
```

## 📁 挂载目录说明

### 1. 应用数据挂载

| 容器路径 | 开发环境挂载 | 生产环境挂载 | 说明 |
|---------|-------------|-------------|------|
| `/app/data` | `douyin_data` volume | `/var/douyin/data` | SQLite数据库文件 |
| `/app/videos` | `douyin_videos` volume | `/var/douyin/videos` | 下载的视频文件 |
| `/app/logs` | `douyin_logs` volume | `/var/douyin/logs` | 应用日志文件 |
| `/app/config.yml` | `./config.yml` | `./config.yml` | 配置文件（只读） |

### 2. Redis数据挂载

| 容器路径 | 开发环境挂载 | 生产环境挂载 | 说明 |
|---------|-------------|-------------|------|
| `/data` | `redis_data` volume | `/var/douyin/redis` | Redis持久化数据 |

### 3. Nginx配置挂载（生产环境）

| 容器路径 | 挂载源 | 说明 |
|---------|--------|------|
| `/etc/nginx/nginx.conf` | `./nginx.conf` | Nginx配置文件 |
| `/etc/letsencrypt` | `/etc/letsencrypt` | SSL证书目录 |
| `/var/www/static` | `./static` | 静态文件目录 |

## 🔧 挂载类型说明

### Docker Volumes (开发环境)
```yaml
volumes:
  - douyin_data:/app/data
```
- **优点**: Docker管理，跨平台兼容，自动备份
- **缺点**: 不易直接访问文件
- **适用**: 开发环境，测试环境

### Bind Mounts (生产环境)
```yaml
volumes:
  - /var/douyin/data:/app/data
```
- **优点**: 直接访问宿主机文件，便于备份和管理
- **缺点**: 路径依赖，权限问题
- **适用**: 生产环境，需要直接文件访问

### 只读挂载
```yaml
volumes:
  - ./config.yml:/app/config.yml:ro
```
- **说明**: `:ro` 表示只读挂载，容器无法修改文件
- **适用**: 配置文件，静态资源

## 🗂️ 目录结构

### 开发环境 Docker Volumes 位置
```
# Windows Docker Desktop
C:\Users\{用户名}\AppData\Local\Docker\wsl\data\ext4.vhdx

# Linux
/var/lib/docker/volumes/

# 查看volume详情
docker volume inspect douyin_data
```

### 生产环境目录结构
```
/var/douyin/
├── data/                 # 数据库文件
│   └── db.sqlite3
├── videos/              # 下载的视频
│   ├── 2024/
│   └── ...
├── logs/                # 应用日志
│   ├── app.log
│   └── error.log
└── redis/               # Redis数据
    ├── appendonly.aof
    └── dump.rdb
```

## 🛠️ 管理命令

### 查看挂载信息
```bash
# 查看容器挂载信息
docker inspect douyin-analysis-app | grep -A 10 "Mounts"

# 查看所有volumes
docker volume ls

# 查看特定volume详情
docker volume inspect douyin_data
```

### 备份数据
```bash
# 备份开发环境数据
docker run --rm -v douyin_data:/data -v $(pwd):/backup alpine tar czf /backup/data_backup.tar.gz -C /data .

# 备份生产环境数据
tar czf douyin_backup_$(date +%Y%m%d).tar.gz -C /var/douyin .
```

### 恢复数据
```bash
# 恢复开发环境数据
docker run --rm -v douyin_data:/data -v $(pwd):/backup alpine tar xzf /backup/data_backup.tar.gz -C /data

# 恢复生产环境数据
tar xzf douyin_backup_20240624.tar.gz -C /var/douyin
```

### 清理数据
```bash
# 删除开发环境volumes
docker-compose down -v

# 删除特定volume
docker volume rm douyin_data

# 清理生产环境数据（谨慎操作）
sudo rm -rf /var/douyin/*
```

## ⚠️ 注意事项

### 1. 权限问题
```bash
# 生产环境创建目录时设置正确权限
sudo mkdir -p /var/douyin/{data,videos,logs,redis}
sudo chown -R 1000:1000 /var/douyin  # 或使用实际用户ID
```

### 2. 磁盘空间
- 视频文件可能占用大量空间，定期清理
- 监控磁盘使用情况
- 考虑使用外部存储

### 3. 备份策略
- 定期备份重要数据
- 测试恢复流程
- 考虑使用自动备份脚本

### 4. Windows路径注意
```yaml
# Windows下使用正斜杠或双反斜杠
volumes:
  - "D:/douyin/data:/app/data"
  # 或
  - "D:\\douyin\\data:/app/data"
```

## 🔍 故障排除

### 挂载失败
```bash
# 检查目录是否存在
ls -la /var/douyin/

# 检查权限
ls -la /var/douyin/data/

# 检查容器内挂载
docker exec -it douyin-analysis-app ls -la /app/data/
```

### 数据丢失
```bash
# 检查volume是否存在
docker volume ls | grep douyin

# 检查挂载点
docker inspect douyin-analysis-app | grep -A 5 "Mounts"
```

### 权限错误
```bash
# 修复权限
sudo chown -R $(id -u):$(id -g) /var/douyin/

# 或使用容器用户ID
sudo chown -R 1000:1000 /var/douyin/
```
