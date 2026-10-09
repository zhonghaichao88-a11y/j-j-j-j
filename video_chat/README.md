# SeeU 视频交友

一对一视频交友 App：网页版 + 安卓 App + iPhone App，三端共用一套前端页面和后端。

```
video_chat/
├── app/            前端页面（手机网页，也是 App 里显示的页面）
├── server/         后端 API（Python FastAPI）+ 实时通道（WebSocket）+ 计费
├── mobile/         安卓 / 苹果 App 外壳（Capacitor），android/ ios/ 是原生工程
├── download/       App 下载页（/download/）
├── deploy/         服务器部署：Docker、HTTPS、PostgreSQL、TURN 中转
└── e2e_preview/    两个浏览器真实互打视频的截图（端到端测试结果）
```

## 一、本地运行

**Windows：双击 `一键启动.bat`**。它会自动检查 Python、安装依赖、生成测试账号、启动服务，然后打开两个浏览器窗口：普通窗口当用户 A，无痕窗口当测试主播 B。需要先装 Python 3.10 或以上，安装时勾选「Add Python to PATH」。

其它系统，或者想手动启动：

```bash
cd video_chat
pip install -r server/requirements.txt
python -m server.seed                 # 可选：生成 6 个「测试主播」账号（上线前用 --clear 删除）
uvicorn server.main:app --port 8000
```

浏览器打开 http://localhost:8000 。开发模式下验证码会直接填好，充值可以用「模拟支付」。
想自己测视频通话：开两个浏览器窗口（一个用无痕模式），分别用两个手机号登录。其中一个号登录 `19900000000`（测试主播），另一个号从发现页点它打视频。

后端测试：`python -m pytest server/tests -q`（9 个测试，覆盖登录 → 认证 → 充值 → 聊天 → 送礼 → 通话扣费 → 评价 → 提现）。

## 二、页面功能 ↔ 后端接口

| 页面 | 功能 | 接口 |
|---|---|---|
| 登录 | 获取验证码 / 登录注册 / 邀请码 | `POST /api/auth/sms`、`POST /api/auth/login` |
| 发现 | 推荐 / 广场 / 活跃 / 附近、所有 / 新人 / 同城 / 亲密度 / 认证 / 在线、筛选 | `GET /api/hosts` |
| 发现 | 轮播图 | `GET /api/banners` |
| 发现 | 排行（魅力榜、富豪榜，日 / 周 / 月） | `GET /api/rank` |
| 主页 | 资料、粉丝、接通率、评分、动态、礼物墙 | `GET /api/users/{id}`（同时记录访客） |
| 主页 | 关注 / 拉黑 / 举报 | `POST /api/users/{id}/follow`、`/block`、`POST /api/reports` |
| 通话 | 视频通话 / 语音通话 / 接听 / 拒绝 / 取消 / 挂断 | `POST /api/calls`、`/calls/{id}/accept`、`reject`、`cancel`、`end` |
| 通话 | 音视频建立连接（WebRTC 信令） | WebSocket `/api/ws`：`signal` |
| 通话 | 按分钟扣费、余额不足自动挂断 | 服务端计时扣费，推送 `balance` / `call_low_balance` / `call_ended` |
| 通话 | 通话中送礼物 | `POST /api/gifts/send`（带 callId） |
| 通话 | 通话结束评价 | `POST /api/calls/{id}/rating` |
| 社区 | 动态 / 同城 / 关注 / 只看认证 | `GET /api/posts` |
| 社区 | 小视频 | `GET /api/posts?kind=reel` |
| 社区 | 发布（图片 / 视频上传、位置、谁可以看） | `POST /api/upload`、`POST /api/posts` |
| 社区 | 点赞 / 评论 / 删除 / 举报 / 分享 | `/api/posts/{id}/like`、`/comments`、`DELETE /api/posts/{id}`、`/api/reports`、`/api/tasks/share` |
| 消息 | 会话列表、未读数、在线的人、清空 | `GET /api/conversations`、`/online-users`、`POST /conversations/read-all` |
| 消息 | 通话记录 | `GET /api/calls` |
| 聊天 | 文字 / 图片 / 语音消息（按住说话） | `POST /api/messages`，实时推送 WebSocket `message` |
| 聊天 | 礼物、清空记录 | `POST /api/gifts/send`、`DELETE /api/conversations/{id}` |
| 客服 / 系统消息 | 常见问题自动回答、人工回复、充值和审核通知 | `/api/notices/service`、`/api/notices/system`、管理接口 `/api/admin/service/{uid}` |
| 我的 | 资料、关注数、粉丝数 | `GET /api/me`、`/me/counts`、`PUT /api/me` |
| 我的 | 充值 / 钱包明细 / 提现 | `GET /api/wallet`、`POST /api/orders`、`POST /api/withdraw` |
| 我的 | VIP（通话 9 折、签到多送、看全部访客、隐身） | `POST /api/orders`（kind=vip） |
| 我的 | 免费赚金币：签到、完善资料、视频认证、分享、邀请 | `/api/tasks/sign`、`/tasks/share`、`/api/invite` |
| 我的 | 免打扰 / 隐私设置 / 美颜设置 / 语言 | `PUT /api/me/settings` |
| 我的 | 视频认证（录制上传）→ 审核 → 开通接听收费、设价格 | `POST /api/verify`、`/api/admin/verify/{uid}`、`PUT /api/me/host` |
| 我的 | 我的动态 / 守护 / 礼物 / 通话评价 / 访问足迹 / 黑名单 | `/api/posts?userId=`、`/me/guards`、`/me/gifts`、`/me/ratings`、`/me/visitors`、`/me/blocks` |
| 我的 | 游戏技能 / 游戏订单 | **未做**，目前显示「即将上线」 |

接口文档（开发模式）：http://localhost:8000/api/docs

### 计费规则（`server/config.py`）

- 打给认证主播：呼叫方付费，价格由主播自己设（5-200 金币/分钟）。接通时先扣第 1 分钟，之后每满 60 秒扣一次；余额不够下一分钟时自动挂断。
- 主播打给普通用户：接听方付费，来电页面会提示价格。普通用户之间通话免费。
- 主播分成 `SEEU_HOST_SHARE=0.5`，收益进入「主播收益」账户，可以申请提现（后台人工打款）。
- VIP 通话打 9 折。所有金币变动都写在 `ledger` 表里，方便对账。

## 三、部署上线（一台云服务器）

```bash
cd video_chat/deploy
cp .env.example .env      # 填域名、公网 IP、各种密码
docker compose up -d --build
```

会启动 4 个服务：后端 API、PostgreSQL、Caddy（自动申请 HTTPS 证书）、coturn（TURN 中转）。
服务器安全组要放行这些端口：80、443、3478（TCP 和 UDP）、49160-49200（UDP）。

建议配置：2 核 4G 起步。音视频是用户之间直连的，只有连不上直连时才走 TURN 中转，所以带宽主要花在 TURN 上。

## 四、打包 App

**安卓**（任选一种）：
- 自动打包：推送代码到 GitHub 后，Actions 里的 `SeeU` 工作流会自动打包 APK（下载 `seeu-apk`）。先在仓库的 Variables 里设置 `SEEU_API=https://你的域名`；正式签名需要在 Secrets 里放签名证书。
- 本地打包：`cd mobile && npm ci && SEEU_API=https://你的域名 npm run android`，会用 Android Studio 打开工程。

打好的 `seeu.apk` 放到 `download/` 目录里，用户访问 `https://你的域名/download/` 就能下载。

**iPhone**：需要一台 Mac + Xcode + 苹果开发者账号（个人或公司，年费 688 元）。运行 `SEEU_API=https://你的域名 npm run ios`，用 Xcode 打开后上传。

> ⚠️ 苹果不允许像安卓那样从网站直接下载安装 App。可走的路只有三条：
> 1. **上架 App Store**（正规途径）。
> 2. **TestFlight 公开测试链接**：最多 1 万人，每个版本 90 天有效，也要经过苹果审核。
> 3. **添加到主屏幕**：用 Safari 打开网站，从分享菜单「添加到主屏幕」。现在就能用，下载页已经写好了引导。
>
> 网上卖的「企业签名」「超级签名」违反苹果规定，随时会被批量封掉，不建议用。

## 五、上线前还需要你去办的事

| 事项 | 现在的状态 | 需要什么 |
|---|---|---|
| 短信验证码 | 开发模式下直接显示 | 阿里云 / 腾讯云短信（要公司资质和签名报备）。接入点：`server/api_account.py` 的 `send_sms` |
| 微信 / 支付宝收款 | 只有模拟支付 | 商户号（要营业执照）。接入点：`POST /api/orders` 下单，`/api/pay/notify/{channel}` 支付回调 |
| iPhone 内购 | 未接 | 苹果规定 iOS App 里买虚拟金币必须走苹果内购（苹果抽成 15-30%），用微信 / 支付宝会被拒审 |
| 离线来电提醒 | 未做 | App 在后台或被关掉时收不到来电，需要接 APNs / FCM / 厂商推送（华为、小米、OPPO、vivo），iPhone 还要接 CallKit |
| 内容审核 | 只有举报 + 后台处理 | 视频交友平台必须有图片、视频、文字的自动审核（阿里云 / 网易易盾），以及人工巡查 |
| 资质 | — | ICP 经营许可证（ICP 证）、网络文化经营许可证、App 备案、等级保护等。必须是公司主体 |
| 法律文本 | 占位 | 用户协议、隐私政策、充值协议、未成年人保护规则，需要律师撰写 |
| 美颜 | 只对自己的预览生效 | 对方看到的画面要真正美颜，需要接美颜 SDK（如腾讯特效、相芯） |
| 多语言 | 只保存了设置 | 翻译文本 |
| 游戏陪玩 | 未做 | 独立的一块业务，要的话可以下一步做 |
| 管理后台网页 | 只有接口，没有页面 | 审核认证、处理举报、给主播打款，目前要调接口操作（`/api/admin/...`），需要做一个后台网页 |
| 实名认证 / 防沉迷 | 未做 | 国内这类平台要求实名（姓名 + 身份证核验），主播收益提现也需要实名 |
| 安卓安装包 | 压缩包里没有 | 要等服务器地址确定后打包；部署好后把域名告诉我即可 |

**还没实际测试过的部分**（代码写好了，但这边的环境没法验证）：

- `一键启动.bat`：只检查了写法，没有在 Windows 上实际运行过。
- 正式部署（`deploy/`）：Docker、PostgreSQL、HTTPS、TURN 中转没有在真实服务器上跑过；本地测试用的是 SQLite。
- 苹果 App：工程已生成，但没有用 Xcode 编译过（需要 Mac）。
- 真手机之间在不同网络下的通话：只在同一台电脑的两个浏览器之间测过，4G 和 WiFi 之间要靠 TURN 中转，部署后需要实测。

## 六、扩容

- 用户量上来后，把 `server/hub.py` 里内存中的在线状态和消息推送换成 Redis 发布订阅，这样可以多台服务器一起跑。
- 上传的文件换成对象存储（阿里云 OSS / 腾讯云 COS）+ CDN。
- 想做多人连麦或直播间，就换成 LiveKit 这类媒体服务器；一对一保持 WebRTC 直连 + TURN 就够了。
