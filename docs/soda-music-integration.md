# 汽水音乐(Soda / Luna)接入设计

> 在 mediahub 后端新增「汽水音乐」(字节跳动,`qishui.douyin.com` / `music.douyin.com`)的解析与下载能力:
> 单曲 / 歌单 / 「我喜欢的音乐」批量下载,支持无损 FLAC 与各档 AAC;并兼顾汽水的 UGC 视频。
>
> Part A 技术原理(逆向要点,语言无关)+ Part B mediahub 接入设计(模块、路由、数据模型、cookie、实施计划)。

---

## 0. 背景与结论

- yt-dlp **不支持**汽水音乐,且音乐音频**加密** → 需自定义 parser + 解密下载器(对标 `douyin_parse/`)。
- 汽水有**两类内容**:音乐 track(加密 m4a/flac)与 UGC 视频(普通 mp4,不加密)。见 §A.2.1。
- mediahub 后端是 **Python**,实测验证的 API + 解密代码**可直接移植**。
- 参考实现(均实测):
  - ✅ `music-lib`(Go,**当前可用**):`soda/{soda,download,crypto,login,playlist,user_playlist}.go` —— 行为蓝本。
  - ⚠️ `musicdl`(Python):`soda.py` 签名失效(只能 30s 试听),但 `utils/sodautils.py::AudioDecryptor` **解密器可直接移植**。
- 实测:VIP cookie 可下完整无损 FLAC(729kbps/44.1kHz/16bit)及各档 AAC;「我喜欢的音乐」210 首可整张批量下载。
- **复用结论**:cookie 复用现有 `user_cookies` 表、下载复用现有路径约定、数据复用 `parsed_media`+`resources` —— **基本零新表、零 migration**(详见 Part B)。

---

# Part A · 技术原理

## A.1 鉴权(Cookie)

走抖音账号体系。获取:登录 `music.douyin.com/qishui/`(抖音扫码)→ 复制 `Cookie` 头;或程序内扫码(参考 `music-lib/soda/login.go`)。
关键字段:`sessionid, sessionid_ss, sid_tt, sid_guard, uid_tt, ttwid, sid_ucp_v1, ssid_ucp_v1, passport_csrf_token`。
> `sessionid`/`sid_guard` 等同登录态,加密存储,严禁明文入库/提交。

## A.2 请求签名(最关键)

`api.qishui.com/luna/pc/*` 用**动态设备参数**做 query;**不需要** `X-Helios`/`X-Medusa`/`a_bogus`(musicdl 因硬编码旧签名失效 → `ERR_REQUEST_FORBIDDEN`)。

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

短链 `qishui.douyin.com/s/XXXX/` HEAD 跟随重定向后,按落点区分:

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
`videoOptions.url` 是 `*.douyinvod.com` 的 `mime_type=video_mp4` 直链,**不加密、无 PlayAuth**,直接下载即可。UGC 视频本质是视频,建议路由到既有视频管线(ytdlp/douyin),不支持时用本页兜底。

> §A.3–A.6 主要针对**音乐 track**(加密、需解密、音频形态),这是与现有能力差异最大、价值最高的部分。

## A.3 接口清单(base `https://api.qishui.com`)

| 接口 | 方法 | 用途 | 关键返回 |
|------|------|------|----------|
| `/luna/pc/me` | GET | 取 `user_id` | `my_info.id` |
| `/luna/pc/user/playlist` | GET | 用户歌单列表(含「我喜欢的音乐」「抖音收藏」) | `playlists[]`,`next_cursor`,`has_more` |
| `/luna/pc/playlist/detail` | GET | 歌单歌曲(分页 `cursor+=count`) | `media_resources[].entity.track` |
| `/luna/pc/track_v2` | POST | 取播放流入口 ★ | `track_player.url_player_info`,`track` |
| `<url_player_info>` | GET | 音质清单 | `Result.Data.PlayInfoList[]` |
| `/luna/pc/search/track` | GET | 搜索 | `result_groups[0].data[].entity.track` |
| `music.douyin.com/qishui/share/track` | GET | 兜底(仅 30s 试听 + 逐字歌词) | `window._ROUTER_DATA` |

- `/track_v2` POST body:`{"track_id","media_type":"track","queue_type":"favorite_track_playlist","scene_name":"library"}`
- `PlayInfoList` 每条:`Quality / Format / Codec / Bitrate / Size / Duration / MainPlayUrl / BackupPlayUrl / PlayAuth`
- 下载地址 = `MainPlayUrl`(空则 `BackupPlayUrl`),指向 `*.douyinvod.com`;**`PlayAuth` 必须保留**(解密密钥载体)。
- 音乐音频(含无损 FLAC)封装在 **MP4 容器**且**加密**。

## A.3.1 一首歌可下载 / 可采集的信息清单(实测 `track_v2.track`)

`track` 顶层字段(实测):`id, album, artists, duration, name, preview, state, stats, vid, label_info, bit_rates, audition_info, song_maker_team, media_type, colors, vocal, tags, ...`

**可下载文件:**
| 资产 | 字段 | 备注 |
|------|------|------|
| 音频(多音质) | `bit_rates` / PlayInfoList | lossless FLAC / hi_res / highest / higher / medium(§A.4) |
| 专辑封面 | `album.url_cover` | 拼 `urls[0] + uri + 尺寸后缀`(如 `~c5_375x375.jpg`) |
| 播放背景图 | `album.url_player_bg` | 大图 / 播放页背景 |
| 歌手头像 | `artists[].url_avatar` | 同样 uri+urls 拼接 |
| 歌词 LRC | track_v2 `lyric` / 分享页 | 逐行 / 逐字(§A.7) |

> 封面/头像 URL 拼接:`url_cover.urls[0] + url_cover.uri + "~c5_375x375.jpg"`(尺寸后缀可调,如 `~c5_720x720.jpg`)。参考 musicdl `soda.py` 的 `cover_url` 拼法。

**可采集元数据(入 `parsed_media` / metadata):**
| 类别 | 字段 |
|------|------|
| 基本 | `name` / `duration`(ms)/ `media_type` / `id` |
| 歌手 | `artists[].{id,name,url_avatar,simple_display_name}` |
| 专辑 | `album.{id,name,release_date,url_cover}` |
| 制作 credits | `song_maker_team`(作词/作曲/编曲等) |
| 主题色 | `colors` / `cover_gradient_effect_color` / `playing_wave_color`(可驱动 UI 配色) |
| 标签 / 人声 | `tags` / `vocal` |
| VIP 策略 | `label_info.{only_vip_download,only_vip_playable,quality_only_vip_can_download/play}` |
| **互动统计** | `stats.{count_collected,count_comment,count_shared}` |
| 收藏状态 | `state.is_collected` |

> ⚠️ **点赞数**(移动端显示 8w+)**不在** PC `track_v2.stats` 里(只有 collected/comment/shared)。如必须展示点赞,需另找移动端接口补;否则先展示「收藏/评论/转发」三项。

## A.4 音质矩阵 & 选择

汽水**无 MP3**;非无损均为 **AAC(m4a)**,无损为 **FLAC(封装在 mp4)**。

| Quality | 编码 | 码率 | | Quality | 编码 | 码率 |
|---------|------|------|--|---------|------|------|
| `lossless`/`hi_fi` | FLAC | ~700–1000k | | `highest` | AAC | ~260k |
| `hi_res` | AAC | ~325k | | `higher` | AAC | ~132k |
| `spatial` | AAC | ~324k | | `medium` | AAC | ~68k |

选择:按目标 `Quality` 精确匹配,匹配不到按 `Bitrate` 降序取最高。
**试听判定**:`stream.Duration + 5 < track.duration` 即为试听 → 应继续找完整流或**显式报错**,绝不静默当成功。

## A.5 解密(音乐 track 必做)

下载到的 `mdat` 是 **MP4 CENC `cenc`、AES-128-CTR、逐样本(可含 subsample 明文/密文分段)** 加密。
完整算法见 `music-lib/soda/crypto.go::DecryptAudio` 与 `musicdl/.../sodautils.py::AudioDecryptor`,要点:

1. **从 PlayAuth 取密钥(Spade)**:`base64_decode` → `paddingLen=(b[0]^b[1]^b[2])-48` → `inner=b[1:len-paddingLen]` → `decryptSpadeInner`(`buff=[0xFA,0x55]+inner`;`out[i]=((inner[i]^buff[i])-popcount(i)-21) mod 255`)→ `skip=base36(out[0])` 切片得 16 字节 hex → AES-128 key。
2. **MP4 盒子**:`moov→trak→mdia→minf→stbl→{stsz,senc,stsd}`,顶层 `mdat`,深层 `tenc`(IV 长度,默认 8)。
3. **逐样本 AES-CTR**:IV 右补到 16B;无 subsample 整块异或;有 subsample 则 `clear` 段原样、`encrypted` 段 CTR 解密。
4. **stsd 修复**:把 `enca` 还原为 `frma` 指示的原始格式(`mp4a`/`fLaC`),否则播放器不识别。

## A.6 后处理(ffmpeg)

- FLAC-in-MP4 → 标准 flac(无损改封装):`ffmpeg -i in.mp4 -c:a copy out.flac`
- 任意 → MP3 320k(转码):`ffmpeg -i in.flac -c:a libmp3lame -b:a 320k out.mp3`
- 纯音频用 `.m4a` 比 `.mp4` 规范。

## A.7 歌词

来源:`track_v2` 关联 `lyric.content`(行级 `[start_ms,dur]<word>`)/ 分享页 `audioWithLyricsOption.lyrics.sentences[]`(逐字 `{startMs, words[].text}`)。合成逐行 / 逐字 LRC。

---

# Part B · mediahub 接入设计

## B.1 现状链路

```
url_router.py(识别平台) → media_service.py(编排) → ytdlp_service.py(主解析器)
                                                  └→ douyin_parse/(抖音自定义兜底)
                          → downloader/downloader.py(下载) → resources(落库)
异步:DBOS workflow + task_tracking
```
汽水音乐 yt-dlp 不支持 + 需解密 → 自定义 parser + 解密下载,对标 `douyin_parse/`。

## B.2 新增模块 `backend/app/services/media/parsers/soda_music/`

| 文件 | 职责 | 来源 |
|------|------|------|
| `soda_api.py` | §A.2 动态签名 + §A.3 接口(track_v2 / playlist / user/playlist / player_info / search / ugc_video) | 移植已验证 Python |
| `soda_decrypt.py` | §A.5 MP4 CENC AES-CTR 解密 + Spade 取密钥 | 移植 musicdl `AudioDecryptor` |
| `soda_quality.py` | §A.4 音质选择 + 试听判定 | 新写 |
| `soda_parser.py` | 编排:URL/track_id → 选音质 → `DownloadInfo`;采集 §A.3.1 元数据/封面 | 新写 |
| `formatter.py` | 映射到 `parsed_media` schema | 仿 `douyin_parse/formatter.py` |
| `__init__.py` | 导出入口 | |

> `soda_decrypt.py` 是纯算法、无外部依赖,优先单测覆盖(PlayAuth + 样本 → 明文)。

## B.3 URL 路由改动 `url_router.py`

```python
PLATFORM_PATTERNS = {
    "qishui": ["qishui.douyin.com", "music.douyin.com"],   # 新增,必须在 douyin 之前判定
    "douyin": ["douyin.com", "iesdouyin.com"],
    ...
}
```
⚠️ `music.douyin.com` 也是 `douyin.com` 子域,**`qishui` 必须优先命中**(放 douyin 前,或精确前缀判断)。`detect_platform` 对 qishui 返回 `handler_type="soda"`。

## B.4 编排分支 `media_service.py`

`platform == "qishui"`(或 `handler_type=="soda"`)时,**跳过 yt-dlp**,先按 §A.2.1 对短链做 HEAD 重定向判定内容类型,再分流:
- **track**(`share/track?track_id=`)→ `soda_parser`(音频,需解密)
  - 单曲 → 一条 `parsed_media`;歌单 / 我喜欢 → 拉全部 track → 批量 `parsed_media`,逐首入队
- **ugc_video**(`share/ugc_video?ugc_video_id=`)→ 视频(普通 MP4,不解密)。优先复用现有视频管线(ytdlp/douyin),不支持时用 `videoOptions.url` 兜底。**非音频核心,可独立小阶段后置。**

## B.5 下载 + 解密流程

音乐音频不能直接落库:`MainPlayUrl` 下载到的是**加密字节**,必须 `soda_decrypt` 后再交给现有 `downloader` 写入 `resources`。
新增 DBOS workflow `soda_download_workflow`(对齐 CLAUDE.md「路线 C」纪律):
- `manager.create()/start()/update_progress()/complete()/fail()` 全走 manager API,失败 `raise`(不 return failed dict)
- 业务字段(歌名/歌手/音质/track_id)写 `task_tracking.metadata`,不写 DBOS input/output
- 步骤:`track_v2 → player_info → 选档 → 下载加密流 → 解密 → (可选 ffmpeg 转封装) → 落 resources`;同时下封面/头像/歌词(§A.3.1)

## B.6 数据模型映射 & 下载落地(**复用现有约定,零/极少 migration**)

> 实测:`parsed_media.source_platform` 是带索引的自由文本(非枚举),加 `'qishui'` **不需要 migration**;音频路径走 migration 085 已确立的 `music_download_path` 约定。

### 字段映射(`parsed_media`)
| 汽水字段 | parsed_media | 备注 |
|----------|--------------|------|
| `track.id` | `platform_id` | source 内唯一 |
| `"qishui"` | `source_platform` | 自由文本,无需 migration |
| `track.name` | `title` | |
| `artists[].name` | `author` / metadata | + 歌手头像 url |
| `album.{name,release_date}` | metadata | |
| `duration`(ms→秒) | `duration` | |
| `album.url_cover` | `cover_download_path` / `cover_url` | 多尺寸,§A.3.1 |
| Quality/Bitrate/Format | metadata | 落档信息 |
| 音频相对路径 | `music_download_path` | 见下 |
| 歌词(LRC) | metadata / 副文件 | §A.7 |
| `song_maker_team` / `colors` / `tags` | metadata | credits / 主题色 / 标签 |
| **点赞 / 评论 / 转发 / 收藏** | metadata 或快照列 | **音频同样有互动数据**(实测),需采集 |

媒体类型为 audio;沿用现有 `media_type`。per-user 归属落 `resources`,`ext` = `flac`/`m4a`/`mp3`。
> ⚠️ 音乐 track **也有 `stats.{count_collected,count_comment,count_shared}`**——不要因为是音频就丢弃互动数据(点赞数 PC 接口缺,见 §A.3.1)。

### 下载落地(沿用现有路径约定)
```
{DOWNLOAD_PATH}/global/resources/web/qishui/{id}/<name>.<ext>
```
- `DOWNLOAD_PATH` 默认 `/app/downloads`(Docker),可由 `frontend_config.yml::default_download_path` 或 `.env` 覆盖(见 `app/core/utils.py`)。
- 路径构造复用 `app/core/utils.py` 的 storage-path helper(`platform='qishui'`, `identifier=id`)。
- 相对路径写入 `parsed_media.music_download_path`(对标 douyin `.../web/douyin/{id}/music.mp3`)。
- 封面/头像也落同目录(`cover.jpg` / `artist.jpg`)。
- 文件回传走现有 file-serving endpoint(`app/main.py`)。

## B.7 Cookie 存储 — **复用现有 `user_cookies` 表(无需新表 / 无需 migration)**

> mediahub 已内置 per-user / per-platform Cookie Management(migration `108_user_cookies.sql`,即 Settings → Cookies,现支持 douyin/bilibili/youtube)。汽水**直接复用**,新增 `platform='qishui'`。**不再建 `soda_credentials` 表。**

```
user_cookies(id, user_id, platform, cookie_text, cookie_file, is_valid, error_message, created_at, updated_at)
UNIQUE(user_id, platform)   -- 已启用 RLS:用户只能管理自己的 cookie
```
- **读取**:`app/repositories/cookies_repository.py::get_by_user_and_platform(user_id, "qishui")`(现成)。
- **保存/校验**:复用 `upsert` + `is_valid`/`error_message`;首存跑一次 §A.4 试听判定写回 `is_valid`(可下完整流=valid)。
- **API**:复用现有 cookie 路由(`app/api/user_settings_router.py`),无需新端点。
- **前端**:现有 Cookie Management 卡片组加一张 "Soda Music" 卡(与 Douyin/Bilibili/YouTube 并列),粘贴 cookie + 显示 Configured/Invalid。
- **加密**:沿用 `user_cookies` 现有口径(与其他平台一致),不为汽水单造方案。

## B.8 前端触点(React + Vite)

- `frontend/types.ts`:`source_platform` 增 `'qishui'`(若有联合类型)。
- `frontend/services/`:`parserService.ts` 复用;**cookie 管理复用现有 service**(加 qishui 平台项,不新建)。
- 解析页:粘贴汽水链接(单曲/歌单/分享短链/UGC 视频)即可,平台识别后:track→soda 音频链路,ugc_video→视频链路。歌单显示曲目列表 + 批量下载;音质下拉(lossless/hi_res/highest...)。
- **音频详情页(`media_type=audio`)**:
  - **新增独立的 "Lyrics" tab**(展示平台自带 LRC,可滚动跟随)。
    > Lyrics ≠ Transcript:Transcript 是 **AI 转写**(Whisper,per-user,有状态),Lyrics 是**平台随歌曲返回的元数据**(无需 AI)。两套独立的东西 —— **Lyrics 是单独新增的 tab,不是把 Transcript 改名/替换**。
  - 视频专属的 **Transcript / Analysis / Summary / Analyze** 对音频**隐藏**。
  - **保留**互动数据卡(Collects / Comments / Shares;点赞视接口补充情况);"Video Duration" 改为音轨时长 + Quality 标签。
  - 主操作:Play(音频播放器)+ Download(音质选择)+ Lyrics + Tags。
- i18n:`public/locales/{en,zh}.json` 加英文 key(如 `lyrics`)。

## B.9 API 端点(前缀 `/api/v1`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/media/fetch` | POST | **复用现有**;URL 命中 qishui 自动走 soda 链路 |
| `/media/soda/playlist` | POST | **新增**;传歌单/我喜欢链接 → 返回曲目列表(供前端勾选) |
| cookie 管理 | — | **复用现有**(`user_settings_router` + `user_cookies`,platform=`qishui`) |

## B.10 实施计划(对齐 CLAUDE.md 分支纪律)

> feature 分支 ≤3 天;先 worktree,小步 PR,`/ship`。

1. **阶段 1 — 核心解析+解密(可单测,零 DB)**:`soda_api.py` + `soda_decrypt.py` + `soda_quality.py`,单测覆盖解密(给样本)+ track_v2 取流。
2. **阶段 2 — 单曲打通**:`url_router`(qishui 优先)+ `media_service` 分支 + `soda_parser` + `formatter` → 单曲落 `parsed_media`(`source_platform='qishui'`)+ 下载解密 + 封面/歌词落 `resources`,路径走 `music_download_path`。**预期零 migration**。
3. **阶段 3 — Cookie 接入**:复用 `user_cookies`(`platform='qishui'`)注入 `soda_api`;前端 Cookie Management 加 "Soda Music" 卡 + 首存试听判定写回 `is_valid`。**无新表、无新端点。**
4. **阶段 4 — 歌单 / 我喜欢批量**:`/media/soda/playlist` + 前端勾选 + 批量入队(DBOS `soda_download_workflow`)。
5. **阶段 5 — 音频详情 UI & 打磨**:Lyrics tab、音频播放器、音质下拉、互动数据卡;ffmpeg 转封装/转码、限频(并发 2–4 + 跳过已下 + `device_id` 每请求新生成)。
6. **(可选)阶段 6 — UGC 视频**:`share/ugc_video` 解析,优先复用现有视频管线。

## B.11 风险 / 注意

- **签名时效**:`3.3.0` 为当前有效;汽水升级 PC 客户端后可能需同步;再现 `ERR_REQUEST_FORBIDDEN` 时优先校对版本号与 `x-luna-*` 头。
- **加密必做**:音乐 track 不解密 = 不可播放;AAC 流同样加密。UGC 视频不加密。
- **VIP 门槛**:`only_vip_download` / `quality_only_vip_can_download` 决定某档是否需会员;非会员对受限曲目只能拿试听 → 必须试听判定并显式报错。
- **限频/风控**:批量串行或低并发,失败重试 + 跳过已下载。
- **合规**:仅供用户对自己已购/会员账号的私人收藏备份,尊重版权,勿分发。

## B.12 参考实现指引

| 关注点 | music-lib (Go, 蓝本) | musicdl (Python, 可移植) |
|--------|----------------------|--------------------------|
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

def get_track_and_play_info(track_id: str, cookie: str, want_quality: str = "hi_res"):
    body = json.dumps({"track_id":track_id,"media_type":"track",
                       "queue_type":"favorite_track_playlist","scene_name":"library"})
    resp = requests.post("https://api.qishui.com/luna/pc/track_v2?" + requests.compat.urlencode(pc_params()),
                         data=body, headers=pc_headers(cookie, post=True), timeout=30).json()
    track = resp.get("track", {})                       # §A.3.1 元数据/封面/stats 都在这
    upi = resp.get("track_player", {}).get("url_player_info")
    info = None
    if upi:
        pl = (requests.get(upi, headers={"User-Agent":UA,"Cookie":cookie}, timeout=30)
              .json().get("Result", {}).get("Data", {}).get("PlayInfoList", []) or [])
        info = (next((p for p in pl if p.get("Quality") == want_quality), None)
                or (max(pl, key=lambda p: p.get("Bitrate", 0)) if pl else None))
    return track, info   # info.MainPlayUrl + info.PlayAuth → 下载 → decrypt_audio()

def cover_url(album_url_cover: dict, size: str = "~c5_375x375.jpg") -> str:
    return (album_url_cover.get("urls", [""])[0]) + album_url_cover.get("uri", "") + size
```

---

*本文基于对 `music-lib`(Go,可用)与 `musicdl`(Python,签名失效但解密可移植)的实测对比整理。
实测覆盖:VIP 完整无损 FLAC / 各档 AAC、「我喜欢的音乐」整张批量、track vs ugc_video 分流、可下载信息清单(封面/头像/歌词/credits/互动统计)。*
