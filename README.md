# BBDown Telegram Bot

这是一个基于 [aiogram](https://github.com/aiogram/aiogram) 开发的 Telegram Bot，其核心下载驱动来自于强大的 [BBDown](https://github.com/nilaoda/BBDown)（Bilibili 下载器）。

本机器人旨在帮助你直接在 Telegram 中接收包含视频、音频、字幕以及弹幕的高画质 B 站内容。同时支持后台定时监控并自动下载特定 UP 主的视频（订阅功能）。

---

## 🚀 特性

- **支持扫码登录**: 完美支持 BBDown 的 TV 端扫码登录功能，轻松获取高画质（1080P/4K）下载权限。
- **解析分享链接**: 直接在聊天框发送 B 站的视频链接或 `b23.tv` 短链，机器人会自动解析并提供画质选择和分P列表。
- **自定义分P下载**: 针对播放列表（多P视频），提供自定义页数范围的下载功能，自动打包上传到对话框。
- **自动订阅 (APScheduler)**: 使用 `/subscribe` 订阅某位 UP 主（支持关键词过滤）。机器人会在后台每隔 30 分钟轮询一次，一旦有新视频自动触发下载并推送到你的 Telegram，完成推送后立刻清理本地存储以防占用磁盘。
- **突破 50MB 限制**: 原生支持部署 Telegram 本地 API Server，理论支持最高发送 2000MB 的单文件。

---

## 🛠️ 快速部署 (Docker Compose 推荐)

为了保证 BBDown 所需的 FFmpeg 环境及数据持久化，建议直接使用 Docker Compose 进行一键部署。该方案将同时为你拉起后端的 Bot 以及配套的 **Telegram Local API Server**，从而彻底解决文件限制问题。

### 1. 准备工作

首先，从 Telegram 官方开发者平台获取必要的参数：
1. 登录 [my.telegram.org](https://my.telegram.org/)
2. 点击 **API development tools**。任意填写 App title 和 short name。
3. 获取网页上给出的 `App api_id` 和 `App api_hash`。

从你的 BotFather 处获取你的机器人 Token（`BOT_TOKEN`），并利用 Rose 或 IDBot 等工具获取你自己的 Telegram 账号数字 ID（`ADMIN_ID`，用于防止陌生人滥用你的机器人）。

### 2. 配置环境变量

复制环境模板文件：
```bash
cp .env.example .env
```
并填入你上一步获取的信息：
```ini
BOT_TOKEN="你的_bot_token"
ADMIN_ID="你的_管理员_chat_id"
BBDOWN_PATH="/usr/local/bin/BBDown"  # 在 Docker 容器内的固定路径，请勿修改
DATA_DIR="data"                      # SQLite和凭证映射目录，请勿修改
API_URL=http://bbdown-telegram-bot-api:8081 # 容器间局域网地址，请勿修改

# Local Telegram Bot API Server (必须填写以支持传输大文件)
TELEGRAM_API_ID=XXXXX      
TELEGRAM_API_HASH=XXXXXXXXXXXXXX
```

### 3. 一键启动

在项目根目录下执行部署指令即可，Docker 会自动寻找环境依赖、下载最新版 Linux 的独立 BBDown 二进制包并启动服务。
```bash
docker-compose up -d --build
```
此时可以运行 `docker logs bbdown-bot -f` 查看机器人启动日志。

---

## 📱 Bot 指令说明

在 Telegram 中找到你的机器人后，点开左下角的 **Menu** 按钮即可快速操作：

- `/login`：触发扫码登录流程。机器人会回传 BBDown 生成的二维码，请使用 B站 App 扫码登录。只有登录后才能下载高画质视频。（登录凭证会自动保存在持久化卷中）
- `/url`：触发链接请求，你也可以直接将视频链接丢给机器人。
- `/subscribe <UID> [可选的标题关键词]`：订阅指定的 B 站 UP 主进行后台监控推送。如果填写了关键词，则仅当视频标题包含该词时才自动下载推送。
- `/unsubscribe`：查看目前的订阅列表并取消订阅。
- `/help`：获取内建帮助提示。

---

## 📦 数据持久化

Docker Compose 文件中将容器的 `/data` 目录映射到了本地的 `./data` 目录。以下数据将得到永久保留：
* `bot.db`：管理你的订阅列表和已自动推送到期的历史下载记录（SQLite引擎）。
* `.data` 文件：BBDown 的登录配置文件，只需扫一次码以后无需重新登录。

## 🙏 鸣谢及核心支持

- [BBDown](https://github.com/nilaoda/BBDown) - 本机器人能够如此强大和稳定的核心下载组件。
- [aiogram](https://github.com/aiogram/aiogram) - 提供极速且符合现代 Python 异步特性的 Telegram Bot API 交互底层。
