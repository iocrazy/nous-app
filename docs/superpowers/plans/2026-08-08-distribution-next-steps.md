# 发布模块 — 后续实现清单

> 交接文档,写于 2026-08-08。上一段工作把 session 发布主干从"一次没跑过"做到
> "连续三次真实发布成功",这份清单是接下来要做的事,按**建议顺序**排列。
>
> 阅读顺序建议:先看「一、当前状态」确认基线,再从「三、A 组」开始动手。

---

## 一、当前状态(基线,动手前先确认还成立)

### 已经真实跑通的

抖音账号 **MioPoo**,连续三次成功发布(probe 4/5/6),抖音后台作品数 71 → 73。
每次都验证到平台侧,不是只看数据库的 `success` 字段。

| 项 | 状态 |
|---|---|
| session 通道发布视频 | ✅ 真跑通 |
| 可见性(仅自己可见) | ✅ 作品卡显示「私密」 |
| 保存权限(不允许下载) | ✅ |
| 封面 | ✅ probe 8 跑通了这一步 |
| profile 抓取(昵称/头像) | ✅ |
| DBOS workflow + task_tracking | ✅ |
| Todolist(Issue)镜像 | ✅ 已存在,见第五节 |
| 反检测:stealth.min.js | ✅ WebGL `SwiftShader → Intel Iris` |
| 反检测:patchright | ✅ 四处 import 全切,Chromium 1208 |

### 验证方法(照抄即可)

```bash
# 1) 发一次真实发布(私密,不会被别人看到)
docker exec nous-backend /app/.venv/bin/python -c "
import asyncio
async def main():
    from app.services.infra import dbos_orchestrator as o
    o.init_dbos_client()          # ← gateway 入队句柄,必须先调
    from app.api.distribution_router import create_task
    from app.schemas.distribution_publish import PublishTaskCreate
    body = PublishTaskCreate(
        content_type='video',
        resource_ids=['336009398064821'],      # 2 秒黑屏测试视频
        title='probe N', description='...',
        visibility='private', allow_download=False,
        distribution_mode='broadcast', channel='session',
        account_ids=['335617669826935'],       # MioPoo
    )
    out = await create_task(body, {'id': '8e1584e3-9c29-4a5b-90fe-125b74259f7f'})
    print('TASK_ID:', out.id)
asyncio.run(main())"

# 2) 跟踪结果(失败时 error_message 里有 [reason] 前缀)
docker exec nous-db psql -U postgres -p 55434 -d postgres -tAc \
  "SELECT status, LEFT(COALESCE(error_message,'-'),200)
   FROM publish_task_accounts WHERE task_id=<TASK_ID>;"

# 3) 去抖音确认(唯一可信判据 —— success 字段不等于作品真的在)
#    脚本见 /tmp/check_vis.py 的思路:用真实 session 打开
#    creator.douyin.com/creator-micro/content/manage,搜标题
```

⚠️ **临时脚本用 `from patchright.async_api`,不是 playwright** —— 容器里现在
只有 patchright 那份 Chromium(1208),用 playwright 会报
`Executable doesn't exist`。

---

## 二、几条硬约定(踩过坑才总结出来的,别绕过)

1. **`success` 字段不算数,去平台确认才算**。整个链路今天有三次"数据库说成功"
   的时刻,只有去抖音后台看到作品卡才是证据。

2. **失败必须报在出错的那一步**。今天最费时间的一次排查:报
   `self_declaration_dialog_missing`,真凶却是上一步的封面弹窗没关掉、盖住了
   控件。**降级成 warning 不是宽容,是把故障转嫁给下游。**

3. **找不到控件就拒绝发布,不要用平台默认值蒙混**。用户要「不允许下载」而控件
   没找到,发出去就是反的;拒绝发布只损失一个草稿。

4. **中文文案定位 > class**。抖音用 CSS Modules,class 是每次发版都变的 hash
   (`radio-d4zkru`)。`get_by_text(exact=True)` 实测五个全部恰好 1 个匹配。
   ⚠️ `exact=True` 不可省:**「允许」是「不允许」的子串**。

5. **猜出来的选择器 = 占位符,不是事实**。`douyin_publish.py` 的 docstring 有
   「验证状态」分级,标着 *Inference* 的那些就是没被真实页面确认过的 —— 而且
   按同一个猜测写的单元测试会长期全绿,制造"有覆盖率"的假象。

6. **测试替身要跟着生产代码走**。换 patchright 时 7 个用例真的去启浏览器了,
   因为打桩写死在 `"playwright.async_api"`。

---

## 三、A 组:已实现但未验证的发布能力(优先做)

这些代码都写了,但**从未在真实发布中执行过**。按风险排序:

### A1. 自主声明(合规相关,最高优先)

- 位置:`browser/app/platforms/douyin_publish.py::_set_self_declaration`
- 六个取值:`内容由AI生成` / `内容为个人观点或见解` / `内容为转载信息` /
  `内容含营销推广信息` / `虚构演绎，仅供娱乐` / `无需添加自主声明`
- **已知**:入口 `请选择自主声明` 和弹窗 `.semi-modal-content` +
  标题 `对作品内容添加声明` 都实测存在且各 1 个匹配 —— **选择器是好的**,
  之前两次失败是被封面弹窗挡住(#1739 已修)
- 验证方法:发一次带 `self_declaration='虚构演绎，仅供娱乐'` 的发布

### A2. 定时发布

- 位置:`_set_schedule`
- 约束:必须带时区、窗口 2h~14d
- ⚠️ 注释里写了:切到「定时发布」会**重新渲染发布按钮所在的区块**,所以它必须
  是点发布前的最后一步
- 验证方法:`scheduled_at=now+3h`,发完去抖音看是不是「待发布」

### A3. 合集

- 位置:`_set_collection`
- 与自主声明的关键差异:**这一步失败只降级不中止**(合集是归档问题,用户事后
  能在平台补;声明是合规问题,不可挽回)。这个不对称是刻意的
- 验证需要账号里先有合集

### A4. 多账号并发(风险最高)

- 从未验证过。涉及 `pg_try_advisory_xact_lock` 的每账号串行
- 需要绑第二个抖音账号才能测
- 关注点:两个账号同时发,会不会抢浏览器槽位、会不会串号

### A5. 公开发布

- 至今只发过「仅自己可见」。公开会触发审核/限流路径,行为可能不同

### 不用做的

**图集 / note**:浏览器侧**根本没实现**,`SUPPORTED_CONTENT_TYPES = ("video",)`
会类型化拒绝。这不是缺口 —— 它不会发出错的东西。要做是新功能,不是修 bug。

---

## 四、B 组:反检测剩余

已完成:stealth.min.js(JS 层)+ patchright(CDP 层),**已追平参考项目 sau**。

剩余,按性价比:

1. **WebRTC 可能绕过 HTTP 代理暴露真实 IP**(安全相关,最该先做)
   - 配了代理反而更可疑:「上海 IP + 上海时区 + 北京真实 IP」的矛盾组合
   - 未验证过是否真的泄露,先测再决定
2. **`chrome.runtime` 仍为 `false`** — stealth 脚本没覆盖到
3. **环境层差距**(做不完,知道即可):容器 Chromium vs 真实 Chrome、
   Xvfb vs 真实桌面、SwiftShader 底层仍是软渲(已被 stealth 伪装外观)
   - sau 用 `channel="chrome"` + 真实桌面,我们做不到"有人在用电脑"
   - 可做的:给容器挂 GPU(这台机器有 NVIDIA 卡)、装真实 Chrome

---

## 五、C 组:Todolist(Issue)接入

**⚠️ 单向镜像已经存在并且在工作**,不要重复实现:

- 位置:`backend/app/workflows/publish_issue_mirror.py`
- 你 2026-07-18 的决定:「发布就应该触发管理」
- 现有行为:派发 → issue 创建;完成 → 自动 done;失败 → blocked(事故泳道);
  pending_share → 保持开启当提醒
- 设计要点:**单向**(只读 task_tracking、只写 issues,从不驱动执行),做成
  scheduled sweeper 而非发布流程里的 hook(崩溃丢失的批次也能补镜像)
- 今天的失败实验已自动生成 `blocked` issue,`origin_id = publish:<task_id>`

**缺的是反方向**:从 issue 发起发布(「在工单里点一下就发布」)。这是新功能:

- 给 issue 一个「执行发布」动作
- 把 issue 的内容/附件映射成发布参数(素材、账号、标题…)
- ⚠️ 设计时注意:**不能破坏单向性**。现有 mirror 之所以安全,正因为它从不
  驱动执行。反向链路要走正规的 `create_task` API,而不是让 issue 直接改
  publish 表

---

## 六、D 组:运维与健壮性(都没验证过)

- session 过期后的自动重登流程
- 代理失效、账号被封的降级路径
- 发布任务超时/中断后的恢复
- **`published_url` 永远为空**:这是抖音客观限制,不是 bug。已验证:发布后
  重定向不含 item id、作品卡无 href/id、列表接口不返回。#1737 给了诚实兜底
  (跳内容管理页,文案「Open in platform」而非「View post」)。
  真正的解法是开放平台 API —— 见下

---

## 七、需要你(用户)在平台侧做的

1. **抖音开放平台**:能力申请仍在审核。通过后 official 通道能拿到真实
   `item_id`,`published_url` 缺口自然消失
2. **Client Secret 已泄露**(出现在早前的截图里),需要在平台重置。新 secret
   请通过 admin UI 录入,不要贴进对话
3. **`system_settings['distribution.douyin']` 记录不存在** —— 阻塞在上面那条

---

## 八、待清理

- 抖音上有 **5 个 probe 测试作品**(probe 4/5/6 成功 + 7/8 失败草稿),全部私密、
  2 秒黑屏。可用作品卡上的「删除作品」逐个删
- 数据库测试数据:`resource_id = 336009398064821`(nous-publish-probe.mp4)
  及其 `resource_items` 行
- Todolist 里两个 `blocked` issue(probe 7/8),可直接关掉

---

## 九、参考

- 设计文档:`docs/superpowers/specs/2026-08-04-distribution-session-channel-design.md`
- 参考项目:`/media/heygo/program/projects-code/github-repos/social-auto-upload`
  - 它用 **patchright + stealth.min.js 双保险**(`pyproject.toml` 是权威,
    `requirements.txt` 里那行 playwright 是过时的次要路径 —— 我为此判断错过一次)
  - 它是**本地工具**:`channel="chrome"` 用真实 Chrome、无 Xvfb、跑在用户桌面。
    我们是服务器无人值守,环境层天然弱于它,这是架构差异不是选型失误
  - 它的代理是全局单一(为翻墙),**我们的每账号独立环境强于它**

### 今天合并的 PR(按时间倒序)

`#1739` 封面弹窗残留 / `#1737` 管理页兜底链接 / `#1736` patchright /
`#1735` stealth / `#1733` 保存权限 radio / `#1732` Ubuntu apt 镜像 /
`#1730` workflow 缺 scope / `#1729` 全 session 批次不该要 OAuth 凭证 /
`#1728` session 校验抓 profile
