#!/bin/bash

# 启动虚拟显示
Xvfb :99 -screen 0 1920x1080x24 &
sleep 2

# 设置显示环境变量
export DISPLAY=:99

# 启动窗口管理器
fluxbox &
sleep 1

# 启动一个终端窗口
xterm -geometry 80x24+10+10 &

# 创建VNC密码文件
mkdir -p ~/.vnc
x11vnc -storepasswd douyin123 ~/.vnc/passwd

# 启动VNC服务器
x11vnc -display :99 -rfbauth ~/.vnc/passwd -listen 0.0.0.0 -rfbport 5902 -xkb -ncache 10 -ncache_cr -forever -shared -bg -noxdamage

# 等待显示服务启动
sleep 2

# 启动应用
exec "$@"
