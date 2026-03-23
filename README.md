---
title: BBDown Telegram Bot
emoji: 🎥
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# BBDown Telegram Bot

在 Telegram 中直接下载 Bilibili 视频、音频、弹幕和字幕，支持多 P 合集、画质选择与 UP 主后台自动订阅推送。

核心下载驱动：[BBDown](https://github.com/nilaoda/BBDown) ｜ Bot 框架：[aiogram](https://github.com/aiogram/aiogram)

---

## ✨ 功能一览

### 下载

- **扫码登录** — 调用 BBDown TV 端扫码，凭证自动持久化，仅需操作一次
- **链接即下载** — 直接发送 B 站视频链接或 `b23.tv` 短链，机器人自动解析
- **画质选择** — 提供「最高画质直出 / 仅提取音频 / 仅提取弹幕」三种下载模式
- **多 P 灵活下载** — 支持下载全部 P、自定义范围（如 `1-3,5,7`）、仅下载第一 P
- **突破文件限制** — 支持部署 Telegram Local API Server，理论发送上限提升至 2GB

### 订阅推送

- **UP 主后台监控** — 机器人每 30 分钟自动轮询，一旦发现新视频立即下载并推送到你的 Telegram
- **关键词过滤** — 可为每个 UP 主设置标题关键词（支持多词、逗号分隔），仅推送命中内容
- **本地视频库** — 订阅后可在 Bot 内浏览该 UP 主所有历史投稿，点击即可下载

---

## 🛠️ 快速部署（Docker Compose 推荐）

> 推荐使用 Docker Compose 一键部署，同时拉起 Bot 和 Telegram Local API Server，彻底解决 50MB 文件发送限制。

### 第一步：准备 Telegram 参数

1. 登录 [my.telegram.org](https://my.telegram.org/)，点击 **API development tools**，填写任意 App 名称，获取 `App api_id` 和 `App api_hash`
2. 向 [@BotFather](https://t.me/BotFather) 发送 `/newbot`，获取机器人 `BOT_TOKEN`
3. 向 [@userinfobot](https://t.me/userinfobot) 或 [@Rose](https://t.me/MissRose_bot) 发送消息，获取你自己的数字 `ADMIN_ID`（机器人仅响应此 ID）

### 第二步：配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```ini
BOT_TOKEN=你的_bot_token
ADMIN_ID=你的数字ID
BBDOWN_PATH=/usr/local/bin/BBDown
DATA_DIR=/data
API_URL=http://bbdown-telegram-bot-api:8081

# Local Telegram Bot API Server（必填，用于突破 50MB 文件限制）
TELEGRAM_API_ID=你的_api_id
TELEGRAM_API_HASH=你的_api_hash
```

### 第三步：启动

```bash
docker-compose up -d --build
```

查看日志：

```bash
docker logs bbdown-bot -f
```

---

## 📱 命令与操作流程

### 首次使用（必须）

1. **发送 `/settings`** 打开控制面板
2. 进入 **登录管理** → **发起扫码登录**（系统会提示你发送 `/login`）
3. 在聊天框发送 **`/login`**，机器人会回传 B 站二维码
4. 打开 B 站 App（TV 版或最新版），扫描二维码
5. 凭证自动保存，后续无需重复登录

### 日常使用

| 操作 | 方法 |
|---|---|
| 下载视频 | 直接发送视频链接或 `b23.tv` 短链，机器人自动解析并引导选择画质 |
| 查看帮助 | 发送 `/help` |
| 管理订阅 | 发送 `/settings` → 进入 **订阅管理** |
| 检查登录状态 | `/settings` → **登录管理** → **查看登录状态** |

### 订阅管理功能

在 `/settings` → **订阅管理** 中你可以：

- **添加订阅** — 输入 UP 主 UID（纯数字），可设置标题关键词过滤
- **浏览本地视频库** — 点击任一 UP 主进入详情页，可浏览已抓取的视频列表并下载
- **刷新视频列表** — 重新用 BBDown 扫描该 UP 主的全部投稿
- **修改关键词** — 随时调整过滤规则
- **删除订阅** — 移除对该 UP 主的监控

---

## 📦 数据持久化

`./data/` 目录（映射到容器内 `/data`）永久保存：

| 文件 | 内容 |
|---|---|
| `bot.db` | SQLite 数据库：订阅列表、视频缓存、下载历史 |
| `BBDown.data` | B 站登录凭证（TV 端扫码登录后生成） |
| `downloads/` | 临时下载目录，文件推送完成后自动清理 |

---

## 🔧 目录结构

```
bbdown_telegrambot/
├── bot/
│   ├── main.py               # 入口：Bot 初始化、/login 命令、调度器启动
│   ├── handlers.py           # 所有消息/按钮处理器及 FSM 状态机
│   ├── database.py           # SQLAlchemy ORM：订阅、视频缓存、下载历史
│   ├── bilibili_api.py       # Bilibili WBI 签名 API 封装
│   ├── bbdown_fetcher.py     # BBDown CLI 封装：扫描 UP 主视频、解析标题
│   ├── subprocess_executor.py # 统一子进程执行器：超时控制、进度解析
│   ├── scheduler.py          # APScheduler 定时任务：订阅轮询与自动推送
│   └── config.py             # 环境变量读取
├── tests/                    # 本地调试脚本（开发用）
├── data/                     # 数据持久化目录（运行时生成）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## 🙏 致谢

- [BBDown](https://github.com/nilaoda/BBDown) — 本机器人强大而稳定的 Bilibili 下载核心
- [aiogram](https://github.com/aiogram/aiogram) — 现代化的异步 Telegram Bot 框架
