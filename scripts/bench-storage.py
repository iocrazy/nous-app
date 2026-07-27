#!/usr/bin/env python3
"""对比 CIFS 文件系统 vs SeaweedFS S3 的真实读性能。

用途:判断媒体存储是否值得从文件系统切到 S3,以及诊断哪一侧卡住了。

    python3 scripts/bench-storage.py

设计要点(都是踩过坑换来的):

1. **字节路径里不能有 Python**。用 Python 读循环测出的 S3 是 243 MB/s,
   换成 curl 立刻变 476 MB/s —— urllib 自己就是天花板。所以吞吐一律外包给
   dd / curl,Python 只负责签名和计时。
2. **必须清 page cache**。同一个文件读第二遍走的是内存,曾测出 CIFS "92 MB/s
   / 0ms" 的假象。每次换文件 + posix_fadvise(DONTNEED)。
3. **必须扫并发**。单流数字看不出瓶颈性质:CIFS 在 1/4/8 并发下是
   118/127/129 MB/s(完全不扩展 = SMB 单连接),S3 是 476/…/581(能扩展)。
   只测单流会把这两种情况混为一谈。
4. **样本要够大**。小文件测的是 IOPS 不是带宽,默认只取 ≥200MB 的对象。

零外部依赖:SigV4 用 hmac/hashlib 手写。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import random
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ── 被测对象 ────────────────────────────────────────────────────────────
CIFS_ROOTS = [
    Path("/mnt/heytime/Sources/nous/media"),
    Path("/mnt/heytime/Sources/nous"),
]
S3_ENDPOINT = os.environ.get("BENCH_S3_ENDPOINT", "http://192.168.8.9:8333")
S3_BUCKET = os.environ.get("BENCH_S3_BUCKET", "nous")
S3_REGION = "us-east-1"

CRED_ENV_FILES = [
    Path("/media/heygo/program/datahub/nous/supabase/.env"),
    Path.home() / ".config/nous/s3.env",
]

SIZE_FLOORS = [200 << 20, 50 << 20, 8 << 20]   # 依次降级,直到样本够
READ_MB = 200                                   # 每流读多少 MB
CONCURRENCY = [1, 4, 8]                         # 并发档位
NEED_SAMPLES = max(CONCURRENCY)

C_OK, C_WARN, C_ERR, C_DIM, C_END = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def hdr(text: str) -> None:
    print(f"\n{text}\n{'─' * 64}")


# ── 网卡 ────────────────────────────────────────────────────────────────
def nic_speed() -> tuple[str, int] | None:
    best = None
    for iface in sorted(Path("/sys/class/net").iterdir()):
        if re.match(r"^(lo|docker|veth|br-|zt|tun|tap)", iface.name):
            continue
        try:
            if (iface / "operstate").read_text().strip() != "up":
                continue
            speed = int((iface / "speed").read_text().strip())
        except (OSError, ValueError):
            continue
        if speed > 0 and (best is None or speed > best[1]):
            best = (iface.name, speed)
    return best


# ── 凭证 ────────────────────────────────────────────────────────────────
def load_credentials() -> tuple[str, str]:
    key, secret = os.environ.get("BENCH_S3_KEY"), os.environ.get("BENCH_S3_SECRET")
    if key and secret:
        return key, secret

    # SEAWEEDFS_* 才是 SeaweedFS 认的身份(compose 里喂给 storage 的 AWS_*)。
    # S3_PROTOCOL_* 是 Supabase 自己对外的 S3 网关凭证,SeaweedFS 不认,会 403。
    wanted = {"SEAWEEDFS_ACCESS_KEY_ID": None, "SEAWEEDFS_SECRET_ACCESS_KEY": None}
    for path in CRED_ENV_FILES:
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() in wanted and wanted[name.strip()] is None:
                wanted[name.strip()] = value.strip().strip("'\"")
        if all(wanted.values()):
            return wanted["SEAWEEDFS_ACCESS_KEY_ID"], wanted["SEAWEEDFS_SECRET_ACCESS_KEY"]

    sys.exit(
        f"{C_ERR}找不到 S3 凭证{C_END}\n"
        f"  找过: {', '.join(str(p) for p in CRED_ENV_FILES)}\n"
        f"  或手动指定: BENCH_S3_KEY=xxx BENCH_S3_SECRET=yyy python3 {sys.argv[0]}"
    )


# ── SigV4(纯标准库) ────────────────────────────────────────────────────
def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign_get(key_path: str, ak: str, sk: str, query: str = "") -> tuple[str, dict[str, str]]:
    """给 SeaweedFS 的 S3 端点签一个 GET(path-style),返回 (url, headers)。"""
    host = S3_ENDPOINT.split("://", 1)[1]
    uri = "/" + S3_BUCKET + ("/" + key_path if key_path else "")
    uri = "/".join(urllib.request.quote(seg, safe="-_.~") for seg in uri.split("/"))

    now = _dt.datetime.now(_dt.timezone.utc)
    amz_date, date_stamp = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        ["GET", uri, query, canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_stamp}/{S3_REGION}/s3/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope,
         hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    signing_key = _sign(_sign(_sign(_sign(f"AWS4{sk}".encode(), date_stamp),
                                    S3_REGION), "s3"), "aws4_request")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    url = f"{S3_ENDPOINT}{uri}" + (f"?{query}" if query else "")
    return url, {
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, "
                         f"SignedHeaders={signed_headers}, Signature={signature}",
    }


def s3_list(ak: str, sk: str, min_size: int, want: int) -> list[tuple[str, int]]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过代理直连内网
    found: list[tuple[str, int]] = []
    token = ""
    for _ in range(30):
        query = "list-type=2&max-keys=1000"
        if token:
            query += "&continuation-token=" + urllib.request.quote(token, safe="")
        query = "&".join(sorted(query.split("&")))  # 签名要求 query 按字典序
        url, headers = sign_get("", ak, sk, query=query)
        req = urllib.request.Request(url)
        for name, value in headers.items():
            req.add_header(name, value)
        with opener.open(req, timeout=30) as resp:
            body = resp.read().decode(errors="replace")
        for m in re.finditer(r"<Key>(.*?)</Key>.*?<Size>(\d+)</Size>", body, re.S):
            if int(m.group(2)) >= min_size:
                found.append((m.group(1), int(m.group(2))))
        if len(found) >= want or "<IsTruncated>true</IsTruncated>" not in body:
            break
        nxt = re.search(r"<NextContinuationToken>(.*?)</NextContinuationToken>", body)
        if not nxt:
            break
        token = nxt.group(1)
    return found


# ── 测量(字节路径全部外包给 dd / curl)──────────────────────────────────
def drop_cache(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except (OSError, AttributeError):
        pass
    finally:
        os.close(fd)


def _run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=False)


def sweep(make_cmd, items, label: str) -> dict[int, float]:
    """对每个并发档位跑一轮,返回 {并发数: 聚合 MB/s}。"""
    results: dict[int, float] = {}
    for n in CONCURRENCY:
        n = min(n, len(items))
        if n == 0:
            continue
        chosen = items[:n]
        for it in chosen:
            if isinstance(it, Path):
                drop_cache(it)
        cmds = [make_cmd(it) for it in chosen]
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(_run, cmds))
        elapsed = time.perf_counter() - start
        if elapsed > 0:
            results[n] = n * READ_MB / elapsed
        print(f"    {label} {n:>2} 并发: {results.get(n, 0):>6.0f} MB/s")
    return results


def find_cifs_files(min_size: int, want: int) -> list[Path]:
    for root in CIFS_ROOTS:
        if not root.is_dir():
            continue
        found = []
        for path in root.rglob("*"):
            try:
                if path.is_file() and path.stat().st_size >= min_size:
                    found.append(path)
            except OSError:
                continue
            if len(found) >= want * 4:
                break
        if len(found) >= want:
            random.shuffle(found)
            return found[:want]
    return []


def verdict(name: str, res: dict[int, float]) -> str:
    """并发能不能把吞吐拉起来,决定瓶颈是"单连接"还是"介质"。"""
    if not res or 1 not in res:
        return ""
    lo, hi = res[1], max(res.values())
    if hi < lo * 1.3:
        return (f"  {C_WARN}{name}: 并发从 1 加到 {max(res)} 只涨 {(hi / lo - 1) * 100:.0f}%"
                f" —— 卡在单连接,不是介质。{C_END}")
    return f"  {C_OK}{name}: 并发可扩展({lo:.0f} → {hi:.0f} MB/s),吞吐受介质而非协议限制。{C_END}"


# ── 主流程 ──────────────────────────────────────────────────────────────
def main() -> int:
    print(f"\n{'═' * 64}\n  存储性能对比:CIFS 文件系统  vs  SeaweedFS S3\n{'═' * 64}")

    hdr("① 链路")
    nic = nic_speed()
    ceiling = 118.0
    if nic:
        name, mbps = nic
        ceiling = mbps / 8 * 0.94
        print(f"  网卡 {name}: {mbps} Mb/s(理论上限 ≈ {ceiling:.0f} MB/s)")
    else:
        print(f"  {C_WARN}⚠ 读不到网卡速率,按千兆估算{C_END}")

    hdr("② 采样")
    ak, sk = load_credentials()
    cifs_files: list[Path] = []
    s3_objects: list[tuple[str, int]] = []
    for floor in SIZE_FLOORS:
        cifs_files = find_cifs_files(floor, NEED_SAMPLES)
        try:
            s3_objects = s3_list(ak, sk, floor, NEED_SAMPLES)
        except Exception as exc:  # noqa: BLE001 — 签名/网络问题都要说清楚
            print(f"  {C_ERR}S3 列举失败: {exc}{C_END}")
            s3_objects = []
        if len(cifs_files) >= NEED_SAMPLES and len(s3_objects) >= NEED_SAMPLES:
            break
    floor_mb = floor >> 20
    print(f"  CIFS: {len(cifs_files)} 个文件   S3: {len(s3_objects)} 个对象   (≥{floor_mb} MB)")
    if not cifs_files or not s3_objects:
        print(f"\n{C_ERR}样本不足,无法对比。{C_END}")
        return 1

    hdr(f"③ 并发扫描(每流读 {READ_MB} MB,字节路径无 Python,已清 page cache)")
    fs_res = sweep(lambda p: f"dd if={shlex.quote(str(p))} of=/dev/null bs=1M count={READ_MB}",
                   cifs_files, "CIFS")
    print()

    def curl_cmd(obj: tuple[str, int]) -> str:
        url, headers = sign_get(obj[0], ak, sk)
        hs = " ".join(f"-H {shlex.quote(f'{k}: {v}')}" for k, v in headers.items())
        # 用 Range 截断而不是管道给 head —— 管道自己会成为瓶颈(实测把单流
        # 476 MB/s 压到 264)。Range 不参与 SigV4 签名,加了不影响鉴权。
        last_byte = READ_MB * 1024 * 1024 - 1
        return (f"curl -s --noproxy '*' -r 0-{last_byte} {hs} "
                f"-o /dev/null {shlex.quote(url)}")

    s3_res = sweep(curl_cmd, s3_objects, "S3  ")

    hdr("④ 结论")
    if not fs_res or not s3_res:
        print(f"  {C_WARN}有一侧没测到,无法下结论。{C_END}")
        return 1

    fs_best, s3_best = max(fs_res.values()), max(s3_res.values())
    print(f"  峰值  CIFS {fs_best:>6.0f} MB/s   |   S3 {s3_best:>6.0f} MB/s"
          f"   |   链路上限 {ceiling:.0f} MB/s\n")
    for line in (verdict("CIFS", fs_res), verdict("S3  ", s3_res)):
        if line:
            print(line)

    ratio = s3_best / fs_best
    print()
    if ratio >= 1.5:
        print(f"  {C_ERR}→ S3 快 {ratio:.1f}× 。CIFS 是瓶颈,不是存储介质。{C_END}")
        print(f"  {C_DIM}    若 CIFS 不随并发扩展,先试挂载加 multichannel,max_channels=4;"
              f"\n    群晖侧也要在 SMB 设置里启用多通道。{C_END}")
    elif ratio >= 0.9:
        print(f"  {C_OK}→ 两者持平(差距 <10%),切 S3 无性能代价。{C_END}")
    elif ratio >= 0.7:
        print(f"  {C_WARN}→ S3 慢 {(1 - ratio) * 100:.0f}%。除非要上 CDN,否则不值得切。{C_END}")
    else:
        print(f"  {C_ERR}→ S3 慢 {(1 - ratio) * 100:.0f}%,明显负优化。别切。{C_END}")

    if max(fs_best, s3_best) > ceiling * 0.85:
        print(f"\n  {C_WARN}⚠ 峰值已达链路 {ceiling:.0f} MB/s 的 85% —— 测的是网卡不是存储。{C_END}")

    print(f"\n  {C_DIM}注:外网访问受家庭上行(约 5 MB/s)限制,以上差距对外网用户不可见。"
          f"\n  只有上 CDN 才能突破上行瓶颈。{C_END}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
