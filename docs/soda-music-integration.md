# 汽水音乐(Soda / Luna)接入设计

> 在 mediahub 后端新增「汽水音乐」(字节跳动,`qishui.douyin.com` / `music.douyin.com`)的解析与下载能力:
> 单曲 / 歌单 / 「我喜欢的音乐」批量下载,支持无损 FLAC 与各档 AAC。
>
> 本文分两部分:**Part A 技术原理**(平台逆向要点,语言无关)+ **Part B mediahub 接入设计**(模块、路由、数据模型、cookie 存储、实施计划)。

---

## 0. 背景与结论

- yt-dlp **不支持**汽水,且汽水音频是**加密**的 → 必须做**自定义 parser + 解密下载器**(对标现有 `douyin_parse/`)。
- mediahub 后端是 **Python**,经实测验证的 API 调用 + 解密代码**可直接移植**,不需重写。
- 两个开源参考实现(均已实测):
  - ✅ `music-lib`(Go,**当前可用**):`soda/{soda,download,crypto,login,playlist,user_playlist}.go` —— 推荐以此为行为蓝本。
  - ⚠️ `musicdl`(Python):`musicdl/modules/sources/soda.py` 签名已失效(只能拿 30s 试听),但其解密器 `musicdl/modules/utils/sodautils.py::AudioDecryptor` **可用、可直接移植**。
- 实测结果:VIP cookie 可下完整无损 FLAC(729kbps / 44.1kHz / 16bit)及各档 AAC;「我喜欢的音乐」210 首可整张拉取批量下载。

---

# Part A · 技术原理

## A.1 鉴权(Cookie)

走抖音账号体系。登录态获取:浏览器登录 `music.douyin.com/qishui/`(抖音扫码)→ 复制 `Cookie` 头;或程序内扫码(参考 `music-lib/soda/login.go`)。
关键字段:`sessionid, sessionid_ss, sid_tt, sid_guard, uid_tt, ttwid, sid_ucp_v1, ssid_ucp_v1, passport_csrf_token`。
> `sessionid` / `sid_guard` 等同登录态,需加密存储,严禁明文入库或提交。

## A.2 请求签名(最关键)

`api.qishui.com/luna/pc/*` 接口用**动态设备参数**做 query;**不需要** `X-Helios`/`X-Medusa`/`a_bogus`(musicdl 因硬编码旧签名失效 → `ERR_REQUEST_FORBIDDEN`)。

实时生成:`now=毫秒时间戳`;`device_id=fp=now`,`iid=now+1`。固定值:

| 参数 | 值 | 参数 | 值 |
|------|-----|------|-----|
| `aid` | `386088` | `version_name` | `3.3.0` |
| `app_name` | `luna_pc` | `version_code` | `30030000` |
| `region`/`geo_region`/`os_region` | `cn` | `channel` | `official` |
| `device_platform` | `windows` | `build_mode` | `master` |
| `device_type` | `Windows` | `ac` | `wifi` |
| `os_version` | `Windows 11` | `tz_name` | `Asia/Shanghai` |

必备请求头:
```
User-Agent: LunaPC/3.3.0(359450208)
x-luna-background-type: foreground
x-luna-is-background-req: 0
x-luna-is-local-user: 1
Cookie: <登录态>
Content-Type: application/json; charset=utf-8   # 仅 POST
```

## A.2.1 汽水内容类型(**两类,必须先分流**)

短链 `qishui.douyin.com/s/XXXX/` HEAD 跟随重定向后,按落点区分内容类型:

| 类型 | 重定向落点 | ID | 取流 | 加密 | 处理 |
|------|-----------|-----|------|------|------|
| **音乐 track** | `share/track?track_id=` | `track_id` | `track_v2`(§A.3) | ✅ CENC AES-CTR(§A.5) | 音频形态,歌词/音质 |
| **UGC 视频** | `share/ugc_video?ugc_video_id=` | `ugc_video_id` | `share/ugc_video` 页内 `videoOptions.url` | ❌ 普通 MP4 直下 | 视频形态,**宜复用现有视频管线** |

**UGC 视频解析**(实测,移动端 UA + cookie):
```
GET https://music.douyin.com/qishui/share/ugc_video?ugc_video_id=<id>
页面 window._ROUTER_DATA → loaderData.ugc_video_page.videoOptions:
  { url(可直下 mp4), videoName, artistName, coverURL, duration, width, height,
    group_download_level, hasCopyright }
```
`videoOptions.url` 是 `*.douyinvod.com` 的 `mime_type=video_mp4` 直链,**不加密、无 PlayAuth**,直接下载即可。UGC 视频本质是视频,建议路由到 mediahub 既有视频管线(ytdlp/douyin),仅在它们不支持时用本页解析兜底。

> 本文 §A.3–A.6 主要针对**音乐 track**(加密、需解密、音频形态),这是与现有能力差异最大、价值最高的部分。

## A.3 接口清单(base `https://api.qishui.com`)

| 接口 | 方法 | 用途 | 关键返回 |
|------|------|------|----------|
| `/luna/pc/me` | GET | 取 `user_id` | `my_info.id` |
| `/luna/pc/user/playlist` | GET | 用户歌单列表(含「我喜欢的音乐」「抖音收藏」) | `playlists[]`,`next_cursor`,`has_more` |
| `/luna/pc/playlist/detail` | GET | 歌单歌曲(分页 `cursor+=count`) | `media_resources[].entity.track` |
| `/luna/pc/track_v2` | POST | 取播放流入口 ★ | `track_player.url_player_info` |
| `<url_player_info>` | GET | 音质清单 | `Result.Data.PlayInfoList[]` |
| `/luna/pc/search/track` | GET | 搜索 | `result_groups[0].data[].entity.track` |
| `music.douyin.com/qishui/share/track` | GET | 兜底(仅 30s 试听 + 逐字歌词) | `window._ROUTER_DATA` |

- `/track_v2` POST body:`{"track_id","media_type":"track","queue_type":"favorite_track_playlist","scene_name":"library"}`
- `PlayInfoList` 每条:`Quality / Format / Codec / Bitrate / Size / Duration / MainPlayUrl / BackupPlayUrl / PlayAuth`
- 下载地址 = `MainPlayUrl`(空则 `BackupPlayUrl`),指向 `*.douyinvod.com`;**`PlayAuth` 必须保留**(解密密钥载体)。
- 短链 `qishui.douyin.com/s/XXXX/` → HEAD 跟随重定向取 `track_id`。

## A.4 音质矩阵 & 选择

汽水**无 MP3**;非无损均为 **AAC(m4a)**,无损为 **FLAC(封装在 mp4)**。

| Quality | 编码 | 码率 | | Quality | 编码 | 码率 |
|---------|------|------|--|---------|------|------|
| `lossless`/`hi_fi` | FLAC | ~700–1000k | | `highest` | AAC | ~260k |
| `hi_res` | AAC | ~325k | | `higher` | AAC | ~132k |
| `spatial` | AAC | ~324k | | `medium` | AAC | ~68k |

选择:按目标 `Quality` 精确匹配,匹配不到按 `Bitrate` 降序取最高。
**试听判定**:`stream.Duration + 5 < track.duration` 即为试听 → 应继续找完整流或**显式报错**,绝不静默当成功。

## A.5 解密(必做)

下载到的 `mdat` 是 **MP4 CENC `cenc`、AES-128-CTR、逐样本(可含 subsample 明文/密文分段)** 加密。
完整算法见 `music-lib/soda/crypto.go::DecryptAudio` 与 `musicdl/.../sodautils.py::AudioDecryptor`,要点:

1. **从 PlayAuth 取密钥(Spade)**:`base64_decode` → `paddingLen=(b[0]^b[1]^b[2])-48` → `inner=b[1:len-paddingLen]` → `decryptSpadeInner`(`buff=[0xFA,0x55]+inner`;`out[i]=((inner[i]^buff[i])-popcount(i)-21) mod 255`)→ 取 `skip=base36(out[0])` 后切片得 16 字节 hex → AES-128 key。
2. **MP4 盒子**:`moov→trak→mdia→minf→stbl→{stsz,senc,stsd}`,顶层 `mdat`,深层 `tenc`(IV 长度,默认 8)。
3. **逐样本 AES-CTR**:IV 右补到 16B;无 subsample 整块异或;有 subsample 则 `clear` 段原样、`encrypted` 段 CTR 解密。
4. **stsd 修复**:把 `enca` 还原为 `frma` 指示的原始格式(`mp4a`/`fLaC`),否则播放器不识别。

## A.6 后处理(ffmpeg)

- FLAC-in-MP4 → 标准 flac(无损改封装):`ffmpeg -i in.mp4 -c:a copy out.flac`
- 任意 → MP3 320k(转码):`ffmpeg -i in.flac -c:a libmp3lame -b:a 320k out.mp3`
- 纯音频用 `.m4a` 比 `.mp4` 规范。

---

# Part B · mediahub 接入设计

## B.1 现状链路回顾

```
url_router.py(识别平台) → media_service.py(编排) → ytdlp_service.py(主解析器)
                                                  └→ douyin_parse/(抖音自定义兜底)
                          → downloader/downloader.py(下载) → resources(落库)
异步:DBOS workflow + task_tracking
```
汽水 yt-dlp 不支持 + 需解密 → 走**自定义 parser + 解密下载**,对标 `douyin_parse/`。

## B.2 新增模块 `backend/app/services/media/parsers/soda_music/`

| 文件 | 职责 | 来源 |
|------|------|------|
| `soda_api.py` | §A.2 动态签名 + §A.3 接口(track_v2 / playlist / user/playlist / player_info / search) | 移植已验证 Python |
| `soda_decrypt.py` | §A.5 MP4 CENC AES-CTR 解密 + Spade 取密钥 | 移植 musicdl `AudioDecryptor` |
| `soda_quality.py` | §A.4 音质选择 + 试听判定 | 新写 |
| `soda_parser.py` | 编排:URL/track_id → 选音质 → `DownloadInfo` | 新写 |
| `formatter.py` | 映射到 `parsed_media` schema | 仿 `douyin_parse/formatter.py` |
| `__init__.py` | 导出 parser 入口 | |

> `soda_decrypt.py` 是纯算法、无外部依赖,优先单测覆盖(给定 PlayAuth + 加密样本 → 明文)。

## B.3 URL 路由改动 `url_router.py`

```python
PLATFORM_PATTERNS = {
    "qishui": ["qishui.douyin.com", "music.douyin.com"],   # 新增,必须在 douyin 之前判定
    "douyin": ["douyin.com", "iesdouyin.com"],
    ...
}
```
⚠️ `music.douyin.com` 也是 `douyin.com` 子域,**`qishui` 必须优先命中**(把 qishui 放在 douyin 前,或对 host 做精确前缀判断)。`detect_platform` 对 qishui 返回 `handler_type="soda"`(新增类型,非 `ytdlp`)。

## B.4 编排分支 `media_service.py`

`platform == "qishui"`(或 `handler_type=="soda"`)时,**跳过 yt-dlp**,先按 §A.2.1 对短链做 HEAD 重定向判定内容类型,再分流:
- **track**(`share/track?track_id=`)→ `soda_parser`(音频,需解密)
  - 单曲链接 → 一条 `parsed_media`
  - 歌单 / 我喜欢链接 → 拉全部 track → 批量 `parsed_media`(每首一条),逐首入下载队列
- **ugc_video**(`share/ugc_video?ugc_video_id=`)→ 视频(普通 MP4,不解密)。优先复用现有视频管线(ytdlp/douyin);不支持时用 `share/ugc_video` 页内 `videoOptions.url` 兜底。**非音频功能的核心,可作为独立小阶段后置。**

## B.5 下载 + 解密流程

汽水音频不能直接落库:`MainPlayUrl` 下载到的是**加密字节**,必须 `soda_decrypt` 后再交给现有 `downloader` 写入 `resources`。
建议新增 DBOS workflow `soda_download_workflow`(对齐 CLAUDE.md「任务系统架构纪律 路线 C」):
- `manager.create()/start()/update_progress()/complete()/fail()` 全走 manager API,失败 `raise`(不 return failed dict)
- 业务字段(歌名 / 歌手 / 音质 / track_id)写 `task_tracking.metadata`,不写 DBOS input/output
- 步骤:`track_v2 → player_info → 选档 → 下载加密流 → 解密 → (可选 ffmpeg 转封装) → 落 resources`

## B.6 数据模型映射 & 下载落地(**复用现有约定,零/极少 migration**)

> 实测:`parsed_media.source_platform` 是带索引的自由文本(非枚举),加 `'qishui'` **不需要 migration**;音频路径走 migration 085 已确立的 `music_download_path` 约定。

### 字段映射(`parsed_media`)
| 汽水字段 | parsed_media | 备注 |
|----------|--------------|------|
| `track.id` | `platform_id` | source 内唯一 |
| `"qishui"` | `source_platform` | 自由文本,无需建枚举/migration |
| `track.name` | `title` | |
| `artists[].name` | `author` / metadata | |
| `album.name` | metadata | |
| `duration` | `duration` | 秒 |
| 封面 url | `cover_download_path` / `cover_url` | |
| Quality/Bitrate/Format | metadata | 落档信息 |
| 音频相对路径 | `music_download_path` | 见下 |
| 歌词(LRC) | metadata / 副文件 | §A.7 |
| **点赞 / 评论 / 转发 / 收藏** | metadata 或对应快照列 | **音频同样有互动数据**(实测 8w+/117/3526),需采集 |

媒体类型为 audio;沿用现有 `media_type`。per-user 归属落 `resources`(复用现有下载落库逻辑),`ext` = `flac`/`m4a`/`mp3`。
> ⚠️ 音乐 track **也有点赞/评论/转发/收藏**,与视频一致——不要因为是音频就丢弃互动数据,采集进快照即可(展示见 B.8)。

### 下载落地(沿用现有路径约定)
```
{DOWNLOAD_PATH}/global/resources/web/qishui/{id}/<name>.<ext>
```
- `DOWNLOAD_PATH` 默认 `/app/downloads`(Docker),可由 `frontend_config.yml::default_download_path` 或 `.env` 覆盖(见 `app/core/utils.py`)。
- 路径构造复用 `app/core/utils.py` 的 storage-path helper(`platform='qishui'`, `identifier=id`)。
- 相对路径写入 `parsed_media.music_download_path`(对标 douyin 的 `.../web/douyin/{id}/music.mp3`)。
- 文件回传走现有 file-serving endpoint(`app/main.py`,按 `download_path`/`music_download_path` 读取)。
- 解密后若要标准 `.flac` / `.mp3`,在落盘前用 ffmpeg 处理(§A.6)。

## B.7 Cookie 存储 — **复用现有 `user_cookies` 表(无需新表 / 无需 migration)**

> mediahub 已内置 per-user / per-platform 的 Cookie Management(migration `108_user_cookies.sql`,即 Settings → Cookies 界面,现支持 douyin/bilibili/youtube)。汽水**直接复用**,新增 `platform='qishui'` 即可。**不再单独建 `soda_credentials` 表。**

现有表结构(已满足需求):
```
user_cookies(id, user_id, platform, cookie_text, cookie_file, is_valid, error_message, created_at, updated_at)
UNIQUE(user_id, platform)   -- 已启用 RLS:用户只能管理自己的 cookie
```
- **读取**:`app/repositories/cookies_repository.py::get_by_user_and_platform(user_id, "qishui")`(现成)。
- **保存/校验状态**:复用现有 `upsert` + `is_valid` / `error_message`;首次保存时跑一次 §A.4 试听判定写回 `is_valid`(VIP/可下完整流 = valid)。
- **API**:复用现有 cookie 管理路由(`app/api/user_settings_router.py`),无需新端点。
- **前端**:在现有 Cookie Management 卡片组里加一张 "Soda Music" 卡(与 Douyin/Bilibili/YouTube 并列),粘贴 cookie + 显示 Configured/Invalid 状态(UI 文案英文)。
- **加密**:沿用现有 `user_cookies` 的存储口径(与 douyin/bilibili/youtube 一致),不为汽水单独造加密方案。

## B.8 前端触点(React + Vite)

- `frontend/types.ts`:`source_platform` 增 `'qishui'`(若有联合类型)。
- `frontend/services/`:`parserService.ts` 复用(URL 入口不变);**cookie 管理复用现有 service**(给 Cookie Management 加 qishui 平台项,不新建 service)。
- Cookie Management UI:在现有卡片组(Douyin/Bilibili/YouTube)加一张 "Soda Music" 卡。
- 解析页:粘贴汽水链接(单曲/歌单/分享短链/UGC 视频)即可,平台识别后:track→soda 音频链路,ugc_video→视频链路。歌单显示曲目列表 + 批量下载;音质下拉(lossless / hi_res / highest...)。
- **音频详情页(`media_type=audio`)调整**:
  - **"Transcript" tab → "Lyrics"**(展示 LRC,可滚动跟随);视频专属的 "Analysis" / "Summary" / "Analyze" 对音频**隐藏**。
  - **保留**互动数据卡(Likes / Comments / Shares / Collects)——音频同样有(见 A / B.6),仅去掉 "Video Duration" 改为音轨时长、Quality 标签。
  - 主操作:Play(音频播放器)+ Download(音质选择)+ Lyrics + Tags。
- i18n:`public/locales/{en,zh}.json` 加文案 key(英文 key,如 `lyrics`)。

## B.9 API 端点(前缀 `/api/v1`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/media/fetch` | POST | **复用现有**;URL 命中 qishui 自动走 soda 链路 |
| `/media/soda/playlist` | POST | **新增**;传歌单/我喜欢链接 → 返回曲目列表(供前端勾选) |
| cookie 管理 | — | **复用现有**(`user_settings_router` + `user_cookies`,platform=`qishui`),不新增端点 |

## B.10 实施计划(对齐 CLAUDE.md 分支纪律)

> feature 分支 ≤3 天;先 worktree,小步 PR,`/ship`。

1. **阶段 1 — 核心解析+解密(可单测,零 DB)**:`soda_api.py` + `soda_decrypt.py` + `soda_quality.py`,单测覆盖解密(给样本)+ track_v2 取流。
2. **阶段 2 — 单曲打通**:`url_router`(qishui 优先)+ `media_service` 分支 + `soda_parser` + `formatter` → 单曲链接落 `parsed_media`(`source_platform='qishui'`)+ 下载解密落 `resources`,路径走 `music_download_path` 约定。**预期零 migration**(source_platform 自由文本;若发现 audio 媒体类型受约束再追加)。
3. **阶段 3 — Cookie 接入**:复用 `user_cookies`(`platform='qishui'`)+ `cookies_repository.get_by_user_and_platform` 注入到 `soda_api`;前端 Cookie Management 加 "Soda Music" 卡 + 首存跑试听判定写回 `is_valid`。**无新表、无新端点。**
4. **阶段 4 — 歌单 / 我喜欢批量**:`/media/soda/playlist` + 前端勾选 + 批量入队(DBOS `soda_download_workflow`,遵守路线 C)。
5. **阶段 5 — 后处理 & 打磨**:ffmpeg 转封装/转码选项、音质下拉、歌词副文件、错误与限频(并发 2–4 + 跳过已下 + `device_id` 每请求新生成)。

## B.11 风险 / 注意

- **签名时效**:版本号 `3.3.0` 为当前有效值;汽水升级 PC 客户端后可能需同步;再现 `ERR_REQUEST_FORBIDDEN` 时优先校对版本号与 `x-luna-*` 头。
- **加密必做**:不解密 = 不可播放;AAC 流同样加密。
- **VIP 门槛**:`only_vip_download` / `quality_only_vip_can_download` 决定某档是否需会员;非会员对受限曲目只能拿试听 → 必须做试听判定并显式报错。
- **限频/风控**:批量串行或低并发,失败重试 + 跳过已下载。
- **合规**:仅供用户对自己已购/会员账号的私人收藏做备份,尊重版权,勿分发。

## B.12 参考实现指引

| 关注点 | music-lib (Go, 推荐蓝本) | musicdl (Python, 可移植部分) |
|--------|--------------------------|------------------------------|
| track_v2 / 取流 | `soda/soda.go: fetchPCTrackV2 / fetchPlayerInfo` | `soda.py: _parsewithofficialapiv1`(签名勿抄) |
| 下载+解密编排 | `soda/download.go: resolveDownloadInfo / Download` | `soda.py: _download` |
| 解密算法 | `soda/crypto.go: DecryptAudio / extractKey` | `utils/sodautils.py: AudioDecryptor / SpadeDecryptor` ✅ |
| 设备参数/UA | `soda/soda.go: sodaPCAppParams / pcRequestOptions` | — |
| 用户歌单/我喜欢 | `soda/user_playlist.go`, `soda/playlist.go` | `soda.py: parseplaylist` |
| 扫码登录 | `soda/login.go` | — |
| VIP 探测 / 音质排序 | `soda/account.go`, `soda.go: sodaQualityRank` | — |

## B.13 参考代码(Python,已验证可跑)

```python
import time, json, requests
# from app.services.media.parsers.soda_music.soda_decrypt import decrypt_audio

UA = "LunaPC/3.3.0(359450208)"

def pc_params() -> dict:
    now = int(time.time() * 1000); dev = str(now)
    return {
        "aid":"386088","app_name":"luna_pc","region":"cn","geo_region":"cn","os_region":"cn",
        "sim_region":"","device_id":dev,"cdid":"","iid":str(now+1),
        "version_name":"3.3.0","version_code":"30030000","channel":"official","build_mode":"master",
        "network_carrier":"","ac":"wifi","tz_name":"Asia/Shanghai","resolution":"",
        "device_platform":"windows","device_type":"Windows","os_version":"Windows 11","fp":dev,
    }

def pc_headers(cookie: str, post: bool = False) -> dict:
    h = {"User-Agent":UA,"Cookie":cookie,
         "x-luna-background-type":"foreground","x-luna-is-background-req":"0","x-luna-is-local-user":"1"}
    if post: h["Content-Type"] = "application/json; charset=utf-8"
    return h

def get_play_info(track_id: str, cookie: str, want_quality: str = "hi_res") -> dict | None:
    body = json.dumps({"track_id":track_id,"media_type":"track",
                       "queue_type":"favorite_track_playlist","scene_name":"library"})
    r = requests.post("https://api.qishui.com/luna/pc/track_v2?" + requests.compat.urlencode(pc_params()),
                      data=body, headers=pc_headers(cookie, post=True), timeout=30)
    upi = r.json().get("track_player", {}).get("url_player_info")
    if not upi: return None
    pl = (requests.get(upi, headers={"User-Agent":UA,"Cookie":cookie}, timeout=30)
          .json().get("Result", {}).get("Data", {}).get("PlayInfoList", []) or [])
    return (next((p for p in pl if p.get("Quality") == want_quality), None)
            or (max(pl, key=lambda p: p.get("Bitrate", 0)) if pl else None))

# 下载:GET MainPlayUrl(UA 头即可)→ decrypt_audio(bytes, PlayAuth) → 落盘
```

---

*本文基于对 `music-lib`(Go,可用)与 `musicdl`(Python,签名失效但解密可移植)的实测对比整理。
完整逆向细节(逐字段)见两仓库源码;本文足以指导 mediahub 阶段 1–5 实施。*
