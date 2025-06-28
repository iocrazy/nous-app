@echo off
setlocal enabledelayedexpansion

:: Docker构建脚本 (Windows版本)
echo [INFO] 开始构建Docker镜像...

:: 检查Docker是否安装
docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker未安装，请先安装Docker Desktop
    pause
    exit /b 1
)

:: 检查docker-compose是否安装
docker-compose --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] docker-compose未安装，请先安装docker-compose
    pause
    exit /b 1
)

:: 切换到项目根目录
cd /d "%~dp0\.."

echo [INFO] 当前目录: %CD%

:: 构建镜像
echo [INFO] 构建Docker镜像...
docker build -t douyin-analysis:latest .

if errorlevel 1 (
    echo [ERROR] Docker镜像构建失败！
    pause
    exit /b 1
)

echo [INFO] Docker镜像构建成功！

:: 询问是否启动服务
set /p choice="是否立即启动服务？(y/n): "
if /i "%choice%"=="y" (
    echo [INFO] 启动Docker Compose服务...
    docker-compose up -d
    
    if errorlevel 1 (
        echo [ERROR] 服务启动失败！
        pause
        exit /b 1
    )
    
    echo [INFO] 服务启动成功！
    echo [INFO] 应用访问地址: http://localhost:8080
    echo [INFO] 健康检查: http://localhost:8080/health
    echo [INFO] 查看日志: docker-compose logs -f
)

echo [INFO] 构建完成！
pause
