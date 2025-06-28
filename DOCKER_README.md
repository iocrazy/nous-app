# Docker 部署指南

本文档介绍如何使用Docker部署抖音分析应用。

## 前置要求

- Docker 20.10+
- Docker Compose 2.0+
- 至少2GB可用内存
- 至少5GB可用磁盘空间

## 快速开始

### 1. 克隆项目
```bash
git clone <your-repo-url>
cd douyin_analysis
```

### 2. 配置环境变量
```bash
# 复制环境变量模板
cp .env.example .env

# 编辑环境变量文件
nano .env
```

### 3. 构建和启动（开发环境）
```bash
# 使用构建脚本
chmod +x scripts/build.sh
./scripts/build.sh

# 或者手动构建
docker-compose up --build -d
```

### 4. 访问应用
- 应用地址: http://localhost:8080
- 健康检查: http://localhost:8080/health
- API文档: http://localhost:8080/docs

## 生产环境部署

### 1. 配置生产环境变量
```bash
# 复制生产环境配置模板
cp .env.example .env.prod

# 编辑生产环境配置
nano .env.prod
```

**重要配置项：**
- `SECRET_KEY`: 更改为安全的密钥
- `NOTION_API_KEY`: 填入您的Notion API密钥
- `NOTION_DATABASE_ID`: 填入您的Notion数据库ID
- `REDIS_PASSWORD`: 设置Redis密码

### 2. 部署到生产环境
```bash
# 使用部署脚本
chmod +x scripts/deploy.sh
./scripts/deploy.sh

# 或者手动部署
docker-compose -f docker-compose.prod.yml up --build -d
```

## Docker命令参考

### 基本操作
```bash
# 构建镜像
docker build -t douyin-analysis:latest .

# 启动服务
docker-compose up -d

# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down

# 重启服务
docker-compose restart
```

### 生产环境操作
```bash
# 启动生产环境
docker-compose -f docker-compose.prod.yml up -d

# 查看生产环境日志
docker-compose -f docker-compose.prod.yml logs -f

# 停止生产环境
docker-compose -f docker-compose.prod.yml down
```

### 数据管理
```bash
# 备份数据库
docker cp douyin-analysis-app:/app/data/db.sqlite3 ./backup_$(date +%Y%m%d_%H%M%S).sqlite3

# 进入容器
docker exec -it douyin-analysis-app bash

# 查看容器资源使用
docker stats douyin-analysis-app
```

## 目录结构

```
douyin_analysis/
├── Dockerfile                 # Docker镜像构建文件
├── docker-compose.yml         # 开发环境编排文件
├── docker-compose.prod.yml    # 生产环境编排文件
├── .dockerignore              # Docker忽略文件
├── .env.example               # 环境变量模板
├── .env.prod                  # 生产环境变量
├── scripts/
│   ├── build.sh              # 构建脚本
│   └── deploy.sh             # 部署脚本
└── ...
```

## 数据持久化

### 开发环境
数据存储在Docker volumes中：
- `douyin_data`: 数据库文件
- `douyin_videos`: 下载的视频文件
- `douyin_logs`: 应用日志
- `redis_data`: Redis数据

### 生产环境
数据存储在宿主机目录中：
- `/var/douyin/data`: 数据库文件
- `/var/douyin/videos`: 下载的视频文件
- `/var/douyin/logs`: 应用日志
- `/var/douyin/redis`: Redis数据

## 监控和日志

### 健康检查
```bash
# 检查应用健康状态
curl http://localhost:8080/health

# 检查Redis健康状态
docker exec douyin-redis redis-cli ping
```

### 日志查看
```bash
# 查看应用日志
docker-compose logs -f douyin-app

# 查看Redis日志
docker-compose logs -f redis

# 查看所有服务日志
docker-compose logs -f
```

## 故障排除

### 常见问题

1. **端口冲突**
   ```bash
   # 检查端口占用
   netstat -tulpn | grep :8080
   
   # 修改docker-compose.yml中的端口映射
   ports:
     - "8081:8080"  # 改为其他端口
   ```

2. **内存不足**
   ```bash
   # 检查容器资源使用
   docker stats
   
   # 调整内存限制
   deploy:
     resources:
       limits:
         memory: 2G  # 增加内存限制
   ```

3. **Chrome浏览器问题**
   ```bash
   # 进入容器检查Chrome
   docker exec -it douyin-analysis-app google-chrome --version
   
   # 查看DrissionPage日志
   docker-compose logs -f douyin-app | grep -i chrome
   ```

### 重置环境
```bash
# 完全清理环境
docker-compose down -v
docker rmi douyin-analysis:latest
docker system prune -f

# 重新构建
docker-compose up --build -d
```

## 安全建议

1. **更改默认密钥**
   - 生成新的SECRET_KEY
   - 设置强密码给Redis

2. **网络安全**
   - 使用防火墙限制访问
   - 配置HTTPS（使用Nginx反向代理）

3. **数据备份**
   - 定期备份数据库
   - 备份重要配置文件

## 性能优化

1. **资源限制**
   - 根据服务器配置调整内存和CPU限制
   - 监控资源使用情况

2. **缓存优化**
   - 配置Redis缓存
   - 优化数据库查询

3. **日志管理**
   - 配置日志轮转
   - 定期清理旧日志

## 支持

如果遇到问题，请：
1. 查看应用日志
2. 检查配置文件
3. 参考故障排除部分
4. 提交Issue到项目仓库
