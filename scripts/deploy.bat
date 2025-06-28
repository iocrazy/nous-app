@echo off
setlocal enabledelayedexpansion

:: 生产环境部署脚本 (Windows版本)
echo [STEP] 开始生产环境部署...

:: 切换到项目根目录
cd /d "%~dp0\.."

:: 检查.env.prod文件是否存在
if not exist ".env.prod" (
    echo [WARNING] .env.prod文件不存在，从模板创建...
    copy .env.example .env.prod
    echo [WARNING] 请编辑.env.prod文件，填入生产环境的配置信息
    pause
    exit /b 1
)

:: 停止现有服务
echo [STEP] 停止现有服务...
docker-compose -f docker-compose.prod.yml down

:: 构建镜像
echo [STEP] 构建生产环境镜像...
docker build -t douyin-analysis:prod .

if errorlevel 1 (
    echo [ERROR] 镜像构建失败！
    pause
    exit /b 1
)

:: 启动生产环境服务
echo [STEP] 启动生产环境服务...
docker-compose -f docker-compose.prod.yml up -d

if errorlevel 1 (
    echo [ERROR] 服务启动失败！
    pause
    exit /b 1
)

:: 等待服务启动
echo [STEP] 等待服务启动...
timeout /t 10 /nobreak >nul

:: 检查服务状态
echo [STEP] 检查服务状态...
curl -f http://localhost/health >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 服务启动失败，请检查日志
    docker-compose -f docker-compose.prod.yml logs
    pause
    exit /b 1
)

echo [INFO] 服务部署成功！
echo [INFO] 应用访问地址: http://localhost
echo [INFO] 健康检查: http://localhost/health

:: 显示服务信息
echo [STEP] 服务信息:
docker-compose -f docker-compose.prod.yml ps

echo [INFO] 部署完成！
echo [INFO] 常用命令:
echo [INFO]   查看日志: docker-compose -f docker-compose.prod.yml logs -f
echo [INFO]   重启服务: docker-compose -f docker-compose.prod.yml restart
echo [INFO]   停止服务: docker-compose -f docker-compose.prod.yml down
pause
