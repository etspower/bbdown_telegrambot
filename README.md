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

核心下载驱动：[BBDown](https://github.com/nilaoda/BBDown) ｜ Bot 框架：[aiogram](https://github.com/aiogram/aiogram)

> **当前部署环境**：加拿大可用区（Oracle Cloud Canada，已脱离中国大陆网络屏蔽范围）。  
> 所有 BBDown 调用默认使用 **`-tv`（TV 端接口）**，无需代理即可正常下载。

---

## ✨ 功能一览

### 下载

- **扫码登录** — 调用 BBDown TV 端扫码，凭证自动持久化，仅需操作一次
- **链接即下载** — 直接发送视频链接或 `b23.tv` 短链，机器人自动解析并引导选择画质
- **画质选择** — 提供最高画质 / 1080P / 720P / 480P / 360P / 仅音频 / 仅弹幕等模式
- **多 P 灵活下载** — 支持下载全部 P、自定义范围（如 `1-3,5,7`）、仅下载第一 P
- **无文件大小限制** — 配合 Telegram Local API Server，发送文件无 50MB 上限（理论最高 4GB）

### 订阅推送

- **UP 主后台监控** — 每 30 分钟自动轮询，新视频立即下载并推送至你的 Telegram
- **关键词过滤** — 可为每个 UP 主设置标题关键词（多词逗号分隔），仅推送命中内容
- **本地视频库** — 订阅后可在 Bot 内浏览 UP 主历史投稿，点击即可下载

---

## 🛠️ 快速部署（Docker Compose）

> Docker Compose 同时拉起 Bot 和 Telegram Local API Server，彻底解决 50MB 文件发送限制。

### 第一步：准备 Telegram 参数

| 参数 | 获取方式 |
|---|---|
| `BOT_TOKEN` | 向 [@BotFather](https://t.me/BotFather) 发送 `/newbot` |
| `ADMIN_ID` | 向 [@userinfobot](https://t.me/userinfobot) 发送任意消息，获取你的数字 ID |
| `TELEGRAM_API_ID` | 登录 [my.telegram.org](https://my.telegram.org) → API development tools |
| `TELEGRAM_API_HASH` | 同上页面获取（必填，用于 Local API Server 发大文件） |

### 第二步：配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入上面的四个参数
```

`.env` 参考内容：

```ini
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_ID=987654321
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash_here
API_URL=http://telegram-api:8081
BBDOWN_PATH=/usr/local/bin/BBDown

# 可选：覆盖 BBDown 全局附加参数（默认 -tv，一般不需要修改）
# BBDOWN_EXTRA_ARGS=-tv
```

### 第三步：启动

```bash
docker compose up -d --build
```

> **注意**：使用 `docker compose`（Docker Compose V2，无连字符）。旧版 Docker 需要 `docker-compose`。

查看 Bot 日志：

```bash
docker compose logs -f bbdown-bot
```

查看 API Server 日志：

```bash
docker compose logs -f telegram-api
```

---

## 📱 命令与操作流程

### 首次使用（必须）

1. 发送 `/settings` 打开控制面板
2. 进入 **登录管理** → **发起扫码登录**（系统会提示你发送 `/login`）
3. 在聊天框发送 **`/login`**，机器人回传 B 站二维码
4. 打开 B 站 App（TV 版或最新版）扫描二维码
5. 凭证自动保存，后续无需重复登录

### 日常使用

| 操作 | 方法 |
|---|---|
| 下载视频 | 直接发送视频链接或 `b23.tv` 短链 |
| 查看帮助 | 发送 `/help` |
| 管理订阅 | 发送 `/settings` → **订阅管理** |
| 检查登录状态 | `/settings` → **登录管理** → **查看登录状态** |

---

## 📦 数据持久化

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
│   ├── main.py               # 入口：Bot 初始化、/login、调度器启动
│   ├── handlers/
│   │   └── download.py       # 消息/按钮处理器、FSM 状态机
│   ├── database.py           # SQLAlchemy ORM
│   ├── bilibili_api.py       # Bilibili WBI 签名 API
│   ├── bbdown_fetcher.py     # BBDown CLI 封装
│   ├── subprocess_executor.py # 子进程执行器：超时、进度解析、-tv 参数注入
│   ├── scheduler.py          # APScheduler：订阅轮询与自动推送
│   └── config.py             # 环境变量读取；BBDOWN_EXTRA_ARGS 在此定义
├── data/                     # 运行时生成，参见上表
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

**为什么使用 `-tv`？**

- TV 端接口不受 Bilibili Web 端 412 反爬限制，在海外服务器（尤其加拿大/美国节点）稳定可用
- 登录后 TV 端凭证可访问大部分 1080P 及以上画质（会员内容需绑定大会员账号）
- 无需 Cloudflare WARP 或其他代理

如需临时切换为 Web 端接口（例如调试），可在 `.env` 中将 `BBDOWN_EXTRA_ARGS` 置空：

```ini
BBDOWN_EXTRA_ARGS=
```

---

## ❓ 常见问题

**Q: 运行时提示 `No such file or directory: '/usr/local/bin/BBDown'`？**  
BBDown 必须在运行环境中安装并可被找到。安装方式取决于你的部署方式：

**方式一：宿主机直接运行（不用 Docker）**
```bash
# Linux/macOS - 下载并安装
wget https://github.com/nilaoda/BBDown/releases/download/1.6.3/BBDown_1.6.3_20240814_linux-x64.zip
unzip BBDown_1.6.3_20240814_linux-x64.zip
sudo mv BBDown /usr/local/bin/
sudo chmod +x /usr/local/bin/BBDown
BBDown --version
```

**方式二：Docker 部署**  
BBDown 在构建镜像时已自动安装到 `/usr/local/bin/BBDown`，无需手动操作。确认 `.env` 中的 `BBDOWN_PATH` 未被手动覆盖。

**手动指定路径（可选）**  
在 `.env` 中设置：
```
BBDOWN_PATH=/your/custom/path/BBDown
```
路径支持绝对路径、相对路径（相对于项目根目录）或纯文件名（从 PATH 查找）。

---

**Q: 机器人回复"解析失败"？**  
检查服务器日志：`docker compose logs bbdown-bot` — 真实 BBDown 错误会记录在 `data/logs/bot.log`。

**Q: 文件发送失败或卡在 0%？**  
确认 `.env` 中填写了 `TELEGRAM_API_ID` 和 `TELEGRAM_API_HASH`，且 `API_URL=http://telegram-api:8081`。  
验证 Local API Server 是否正常运行：`curl http://localhost:8081`.

**Q: 二维码扫描后仍然提示未登录？**  
登录凭证保存在 `data/BBDown.data`。重新扫码会覆盖旧凭证。确认容器对该文件有写权限。

**Q: 下载时提示 412 错误？**  
确认 `BBDOWN_EXTRA_ARGS=-tv` 已生效（查看日志中的 `🔧 BBDown 最终命令` 是否包含 `-tv`）。  
若服务器位于中国大陆网络，建议迁移至海外可用区（加拿大、新加坡等）。

**Q: Docker 构建时卡住，报 `Could not resolve deb.debian.org`？**  
服务器 DNS 解析失败（Oracle Cloud 等云平台常见）。先试：

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
发送链接后按提示按钮选择，或直接输入 `1-3,5,7`（下载第 1-3 P 和第 5、7 P）。

---

## 🙏 致谢

- [BBDown](https://github.com/nilaoda/BBDown) — 本机器人强大而稳定的 Bilibili 下载核心
- [aiogram](https://github.com/aiogram/aiogram) — 现代化的异步 Telegram Bot 框架
