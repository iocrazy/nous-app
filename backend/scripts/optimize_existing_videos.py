#!/usr/bin/env python3
"""
批量优化已下载的视频文件，使其支持流式播放。

使用 ffmpeg 的 -movflags faststart 将 moov atom 移到文件开头，
使视频可以在下载完成前就开始播放，大幅减少远程播放的等待时间。

使用方法:
    python scripts/optimize_existing_videos.py /path/to/videos

示例:
    python scripts/optimize_existing_videos.py /app/downloads
    python scripts/optimize_existing_videos.py ./downloads --dry-run
"""

import os
import sys
import argparse
import subprocess
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


def check_ffmpeg():
    """检查 ffmpeg 是否安装"""
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def is_already_optimized(file_path: str) -> bool:
    """
    检查视频是否已经优化（moov atom 在文件开头）

    通过 ffprobe 检查 moov atom 的位置
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format_tags=major_brand",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # 简单检查：如果能快速读取元数据，可能已经优化了
        # 更准确的方法需要解析 atom 位置
        return False  # 保守起见，总是尝试优化
    except Exception:
        return False


def optimize_video(file_path: str, dry_run: bool = False) -> dict:
    """
    优化单个视频文件

    Returns:
        dict: 包含 success, message, file_path
    """
    result = {"file_path": file_path, "success": False, "message": ""}

    if not file_path.endswith(".mp4"):
        result["message"] = "Skipped: not MP4"
        result["success"] = True
        return result

    if not os.path.exists(file_path):
        result["message"] = "File not found"
        return result

    original_size = os.path.getsize(file_path)

    if dry_run:
        result["success"] = True
        result["message"] = f"Would optimize ({original_size / 1024 / 1024:.1f} MB)"
        return result

    temp_path = file_path + ".optimizing.mp4"

    try:
        # 运行 ffmpeg 优化
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                file_path,
                "-c",
                "copy",
                "-movflags",
                "faststart",
                temp_path,
            ],
            capture_output=True,
            text=True,
            timeout=600,  # 10 分钟超时
        )

        if proc.returncode == 0 and os.path.exists(temp_path):
            optimized_size = os.path.getsize(temp_path)

            # 检查优化后文件大小是否合理
            if optimized_size >= original_size * 0.95:
                # 替换原文件
                shutil.move(temp_path, file_path)
                result["success"] = True
                result["message"] = f"Optimized ({original_size / 1024 / 1024:.1f} MB)"
            else:
                result["message"] = (
                    f"Size mismatch: {optimized_size} vs {original_size}"
                )
                os.remove(temp_path)
        else:
            result["message"] = (
                f'ffmpeg failed: {proc.stderr[:100] if proc.stderr else "Unknown"}'
            )
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except subprocess.TimeoutExpired:
        result["message"] = "Timeout"
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception as e:
        result["message"] = f"Error: {str(e)}"
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return result


def find_videos(directory: str) -> list:
    """递归查找所有 MP4 文件"""
    videos = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".mp4") and not file.endswith(".optimizing.mp4"):
                videos.append(os.path.join(root, file))
    return videos


def main():
    parser = argparse.ArgumentParser(
        description="批量优化视频文件以支持流式播放",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("directory", help="视频文件目录")
    parser.add_argument(
        "--dry-run", action="store_true", help="只显示会处理的文件，不实际修改"
    )
    parser.add_argument("--workers", type=int, default=2, help="并行处理数量（默认 2）")

    args = parser.parse_args()

    if not check_ffmpeg():
        print("❌ ffmpeg 未安装，请先安装 ffmpeg")
        print("   Ubuntu/Debian: sudo apt install ffmpeg")
        print("   macOS: brew install ffmpeg")
        sys.exit(1)

    if not os.path.isdir(args.directory):
        print(f"❌ 目录不存在: {args.directory}")
        sys.exit(1)

    print(f"🔍 正在扫描目录: {args.directory}")
    videos = find_videos(args.directory)

    if not videos:
        print("📭 没有找到 MP4 文件")
        sys.exit(0)

    print(f"📹 找到 {len(videos)} 个视频文件")

    if args.dry_run:
        print("\n🔍 Dry run 模式 - 不会修改任何文件\n")

    success_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(optimize_video, video, args.dry_run): video
            for video in videos
        }

        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            status = "✅" if result["success"] else "❌"
            filename = os.path.basename(result["file_path"])
            print(f'[{i}/{len(videos)}] {status} {filename}: {result["message"]}')

            if result["success"]:
                success_count += 1
            else:
                fail_count += 1

    print(f"\n📊 完成: {success_count} 成功, {fail_count} 失败")


if __name__ == "__main__":
    main()
