---
title: BBDown Telegram Bot
emoji: 🎥
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# BBDown Telegram Bot

在 Telegram 中直接下载 Bilibili 视频、音频、弹幕和字幕，支持多 P 合集、画质选择与 UP 主后台订阅推送。

核心下载驱动：[BBDown](https://github.com/nilaoda/BBDown) ｜ Bot 框架：[aiogram](https://github.com/aiogram/aiogram) ｜ 订阅源：[RSSHub](https://github.com/DIYgod/RSSHub)

> 所有 BBDown 调用默认使用 **`-tv`（TV 端接口）**。

---

## ✨ 功能一览

### 下载

- **扫码登录** — 调用 BBDown TV 端扫码，凭证自动持久化，仅需操作一次
- **链接即下载** — 直接发送视频链接或 `b23.tv` 短链，机器人自动解析并引导选择画质
- **画质选择** — 最高画质 / 1080P / 720P / 480P / 360P / 仅音频 / 仅弹幕
- **多 P 灵活下载** — 支持全部 P、自定义范围（如 `1-3,5,7`）、仅第一 P
- **无文件大小限制** — 配合 Telegram Local API Server，发送文件无 50MB 上限（理论最高 4GB）

### 订阅推送

- **UP 主后台监控** — 每 30 分钟通过 **RSSHub** 轮询，新视频立即下载并推送至 Telegram
- **关键词过滤** — 为每个 UP 主设置标题关键词（多词逗号分隔），仅推送命中内容
- **本地视频库** — 订阅后可在 Bot 内浏览 UP 主历史投稿，点击即可下载

---

## 📦 系统架构

```
┌─────────────────┐   内网通信
│  bbdown-bot     ├───────► telegram-api :8081  (发送大文件用)
│  (Python Bot)  └───────► rsshub       :1200  (订阅轮询用)
└─────────────────┘
```

| 容器 | 镜像 | 作用 |
|---|---|---|
| `bbdown-bot` | 本仓自建 | Bot 主体，处理所有消息和下载 |
| `telegram-api` | `aiogram/telegram-bot-api` | Telegram Local API Server，解除 50MB 限制 |
| `rsshub` | `diygod/rsshub` | 提供 UP 主最新投稿 RSS，绕过 B 站风控 |

---

## 🚀 部署指南

### 方式一：全容器化（生产推荐）

一条命令展开三个容器。

#### 第一步：准备参数

| 参数 | 获取方式 |
|---|---|
| `BOT_TOKEN` | 向 [@BotFather](https://t.me/BotFather) 发送 `/newbot` |
| `ADMIN_ID` | 向 [@userinfobot](https://t.me/userinfobot) 发送任意消息，获取你的数字 ID |
| `TELEGRAM_API_ID` | 登录 [my.telegram.org](https://my.telegram.org) → API development tools |
| `TELEGRAM_API_HASH` | 同上页面获取 |

#### 第二步：创建 `.env`

```bash
cp .env.example .env
vim .env   # 或用任意编辑器
```

最小必填配置（其余可保持默认值）：

```ini
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_ID=987654321
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
```

> `API_URL`、`BBDOWN_PATH`、`RSSHUB_BASE_URL` 在全容器化模式下已由 `docker-compose.yml` 配好，无需在 `.env` 中设置。

#### 第三步：启动

```bash
docker compose up -d --build
```

查看各容器状态：

```bash
docker compose ps
```

查看 Bot 日志：

```bash
docker compose logs -f bbdown-bot
```

查看 RSSHub 日志：

```bash
docker compose logs -f rsshub
```

---

### 方式二：调试模式（Python 直运行 + 部分容器）

适合本地开发调试：Bot 主体用 Python 直接运行，`telegram-api` 和 `rsshub` 用 Docker 运行并暴露本地端口。

#### 第一步：启动两个服务容器

```bash
# 仅启动 telegram-api 和 rsshub，不启动 bbdown-bot
docker compose up -d telegram-api rsshub
```

验证 rsshub 是否正常（用任意 UP 主 UID 测试）：

```bash
curl "http://localhost:1200/bilibili/user/video/你的UID" | head -40
# 正常应返回 XML ，包含 <item> 条目和 BV 号
```

#### 第二步：配置 `.env`

```ini
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_ID=987654321
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef

# 调试模式：连到宿主机暴露端口
API_URL=http://localhost:8081
RSSHUB_BASE_URL=http://localhost:1200

# BBDown 二进制在宿主机上的路径
BBDOWN_PATH=/usr/local/bin/BBDown
```

#### 第三步：安装依赖并运行 Bot

```bash
pip install -r requirements.txt
python -m bot
```

#### 切换到全容器化

调试完成后，移除或注释 `.env` 中的 `API_URL` 和 `RSSHUB_BASE_URL`，然后：

```bash
docker compose up -d --build
```

`docker-compose.yml` 内的环境变量会自动接管（`http://telegram-api:8081`、`http://rsshub:1200`），无需任何修改。

---

## 📝 完整 `.env` 配置参考

```ini
# =====================================================================
# Telegram Bot 基础配置
# =====================================================================

# Bot Token — 从 @BotFather 获取
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# 管理员 Telegram 用户 ID — 仅该用户可与 Bot 交互
ADMIN_ID=987654321

# =====================================================================
# Telegram Local API Server — 必填，用于发送超过 50MB 的文件
# =====================================================================

# 从 https://my.telegram.org 获取
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef

# Local API Server 地址
# 全容器化：http://telegram-api:8081  (已由 docker-compose.yml 设好，无需填写)
# 调试模式：http://localhost:8081
API_URL=http://localhost:8081

# =====================================================================
# BBDown 配置
# =====================================================================

# BBDown 可执行文件路径
# 全容器化：无需设置，默认 /usr/local/bin/BBDown (已由 docker-compose.yml 设好)
# 调试模式：填写 BBDown 在宿主机上的实际路径
BBDOWN_PATH=/usr/local/bin/BBDown

# BBDown 全局附加参数 — 默认 -tv，一般无需修改
# -tv 使用 TV 端接口，无需大会员并可规避部分 412 错误
# 设置为空可切换回 Web 端接口（调试用）
BBDOWN_EXTRA_ARGS=-tv

# =====================================================================
# RSSHub 配置 — 订阅轮询数据源
# =====================================================================

# RSSHub 实例地址
# 全容器化：http://rsshub:1200  (已由 docker-compose.yml 设好，无需填写)
# 调试模式：http://localhost:1200
RSSHUB_BASE_URL=http://localhost:1200

# =====================================================================
# 调度器配置 — 可选
# =====================================================================

# 每次订阅轮询最多检查的页数（RSS 首页通常已足够，默认 2）
# SCHEDULER_MAX_PAGES=2

# =====================================================================
# 数据目录 — 可选
# =====================================================================

# 持久化数据目录路径
# 全容器化：无需设置，自动解析为项目根目录下的 data/
# 调试模式：可自定义为任意可写路径
# DATA_DIR=/path/to/your/data
```

### 配置分析对照表

| 变量 | 全容器化 | 调试模式 | 备注 |
|---|---|---|---|
| `BOT_TOKEN` | 必填 | 必填 | 是 |
| `ADMIN_ID` | 必填 | 必填 | 是 |
| `TELEGRAM_API_ID` | 必填 | 必填 | 是 |
| `TELEGRAM_API_HASH` | 必填 | 必填 | 是 |
| `API_URL` | 无需填写 | `http://localhost:8081` | docker-compose.yml 已设 |
| `BBDOWN_PATH` | 无需填写 | 宿主机实际路径 | docker-compose.yml 已设 |
| `RSSHUB_BASE_URL` | 无需填写 | `http://localhost:1200` | docker-compose.yml 已设 |
| `BBDOWN_EXTRA_ARGS` | 可选 | 可选 | 默认 `-tv` |
| `SCHEDULER_MAX_PAGES` | 可选 | 可选 | 默认 `2` |
| `DATA_DIR` | 可选 | 可选 | 默认自动解析 |

---

## 📱 命令与操作流程

### 首次使用（必须）

1. 发送 `/settings` 打开控制面板
2. 进入 **登录管理** → **发起扫码登录**
3. 在聊天框发送 **`/login`**，机器人回传 B 站二维码
4. 打开 B 站 App 扫描二维码
5. 凭证自动保存，后续无需重复登录

### 日常使用

| 操作 | 方法 |
|---|---|
| 下载视频 | 直接发送视频链接或 `b23.tv` 短链 |
| 查看帮助 | 发送 `/help` |
| 管理订阅 | 发送 `/settings` → **订阅管理** |
| 检查登录状态 | `/settings` → **登录管理** → **查看登录状态** |

---

## 🗄️ 数据持久化

`./data/` 目录（映射到容器内 `/app/data`）永久保存：

| 路径 | 内容 |
|---|---|
| `bot.db` | SQLite：订阅列表、视频缓存、下载历史 |
| `BBDown.data` | B 站登录凭证（扫码后生成） |
| `downloads/` | 临时下载目录，推送完成后自动清理 |
| `telegram-api/` | Telegram Local API Server 数据 |

---

## 🔧 目录结构

```
bbdown_telegrambot/
├── bot/
│   ├── main.py                # 入口：Bot 初始化、/login、调度器启动
│   ├── handlers/
│   │   ├── download.py          # 消息/按鈕处理器、FSM 状态机
│   │   └── subscription.py      # 订阅管理 UI
│   ├── database.py            # SQLAlchemy ORM
│   ├── bilibili_api.py        # Bilibili WBI 签名、UP 主信息查询
│   ├── rss_fetcher.py         # RSSHub 调用 + RSS XML 解析（订阅轮询用）
│   ├── bbdown_fetcher.py      # BBDown CLI 封装，批量拖取资源
│   ├── subprocess_executor.py # 子进程执行器：超时、进度解析
│   ├── scheduler.py           # APScheduler：订阅轮询与自动推送
│   └── config.py              # 环境变量读取，全局常量定义
├── data/                      # 运行时生成，参见上表
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## ⚙️ BBDown 参数说明

| 参数 | 说明 | 默认值 |
|---|---|---|
| `BBDOWN_EXTRA_ARGS` | 注入到所有 BBDown 调用的全局附加参数 | `-tv` |
| `BBDOWN_PATH` | BBDown 可执行文件路径 | `BBDown`（从 `PATH` 查找） |

如需临时切换为 Web 端接口（例如调试），可在 `.env` 中将 `BBDOWN_EXTRA_ARGS` 置空：

```ini
BBDOWN_EXTRA_ARGS=
```

---

## ❓ 常见问题

**Q: 订阅轮询收到“连接失败”提示？**  
RSSHub 容器未启动或地址配置错误。检查：
- 全容器化：`docker compose ps rsshub`，状态应为 `healthy`
- 调试模式：确认 `.env` 中 `RSSHUB_BASE_URL=http://localhost:1200`，并执行 `curl http://localhost:1200/healthz`

**Q: 运行时提示 `No such file or directory: '/usr/local/bin/BBDown'`？**  

全容器化模式下 BBDown 在构建镜像时自动安装，无需手动操作。调试模式下需在宿主机安装：

```bash
# Linux/macOS
wget https://github.com/nilaoda/BBDown/releases/latest/download/BBDown_linux-x64.zip
unzip BBDown_linux-x64.zip
sudo mv BBDown /usr/local/bin/ && sudo chmod +x /usr/local/bin/BBDown
BBDown --version
```

**Q: 机器人回复“解析失败”？**  
查看日志了解真实错误：

```bash
docker compose logs bbdown-bot
```

**Q: 文件发送失败或卡在 0%？**  
确认 `TELEGRAM_API_ID` 和 `TELEGRAM_API_HASH` 已填写，且 `telegram-api` 容器正常运行：

```bash
curl http://localhost:8081
```

**Q: 二维码扫描后仍然提示未登录？**  
登录凭证保存在 `data/BBDown.data`。重新扫码会覆盖旧凭证。确认容器对该文件有写权限。

**Q: 下载时提示 412 错误？**  
确认 `BBDOWN_EXTRA_ARGS=-tv` 已生效（查看日志中的 BBDown 命令是否包含 `-tv`）。可能为 B 站屏蔽服务器 IP，需要切换到未屏蔽地址。

**Q: Docker 构建时卡住，报 `Could not resolve deb.debian.org`？**  
DNS 解析失败（Oracle Cloud 等云平台常见）：

```bash
# 方法 1：给 Docker 配置 Google DNS
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{"dns": ["8.8.8.8", "8.8.4.4"]}
EOF
sudo systemctl restart docker
docker compose up -d --build
```

```bash
# 方法 2：放行 iptables FORWARD（Oracle Cloud）
sudo iptables -P FORWARD ACCEPT
docker compose up -d --build
```

**Q: 多 P 下载如何指定范围？**  
发送链接后按提示按鈕选择，或直接输入 `1-3,5,7`（下载第 1-3 P 和第 5、7 P）。

---

## 🙏 致谢

- [BBDown](https://github.com/nilaoda/BBDown) — 本机器人强大而稳定的 Bilibili 下载核心
- [aiogram](https://github.com/aiogram/aiogram) — 现代化的异步 Telegram Bot 框架
- [RSSHub](https://github.com/DIYgod/RSSHub) — 提供稳定的 UP 主订阅 RSS 源
