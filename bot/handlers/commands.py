"""
bot/handlers/commands.py - 基础命令处理

包含：
- /start
- /help
- /url
- /login (触发提示)
"""

from aiogram import Router, types
from aiogram.filters import CommandStart, Command

from bot.config import is_admin

router = Router()
router.message.filter(lambda msg: msg.from_user is not None and is_admin(msg.from_user.id))


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Hello! Send me a Bilibili link or use /login to authenticate BBDown."
    )


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 **BBDown Telegram Bot 使用帮助**\n\n"
        "**1. 账号登录（必须）**\n"
        "发送 `/settings` → 登录管理 → 发起扫码登录 → 发送 `/login` 触发二维码生成。"
        "请使用 B站 App（TV 版）扫码。凭证自动保存，后续无需重复登录。\n\n"
        "**2. 如何下载视频？**\n"
        "- **直接发送链接**：将 B站视频链接（支持 b23.tv 短链）直接发给机器人。\n"
        "- **使用命令**：或发送 `/url` 后输入。\n"
        "机器人解析后会提供画质选择，确认后开始下载并回传文件。\n\n"
        "**3. 多 P 视频下载**\n"
        "合集视频支持：下载全部 P、自定义范围（如 `1-3,5,7`）、仅下载第一 P。\n\n"
        "**4. 自动订阅 UP 主**\n"
        "发送 `/settings` → 订阅管理 → 添加新订阅（输入 UP 主 UID）。\n"
        "- 支持设置标题关键词过滤（逗号分隔，多词命中即推送）\n"
        "- 机器人每 30 分钟后台轮询，有新视频自动下载推送\n"
        "- 可在订阅详情页浏览历史投稿列表，随时点击下载\n\n"
        "**5. 常见问题**\n"
        "- 登录失效：`/settings` → 登录管理 → 发起扫码登录 重新认证\n"
        "- 文件大小限制：使用 Docker Compose 部署（已包含 Local API Server），单文件最高支持 2GB\n\n"
        "💡 *本机器人基于开源项目 [BBDown](https://github.com/nilaoda/BBDown) 驱动。*"
    )
    await message.answer(help_text, parse_mode="Markdown", disable_web_page_preview=True)


@router.message(Command("url"))
async def cmd_url(message: types.Message):
    await message.answer(
        "🔗 请直接发送你需要下载的 Bilibili 视频链接，例如：\n"
        "`https://www.bilibili.com/video/BV1xx411c7mD`\n"
        "或分享短链 `https://b23.tv/...`",
        parse_mode="Markdown"
    )


# Legacy commands gracefully redirected
@router.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message):
    await message.answer(
        "ℹ️ 订阅功能已全新升级，请使用 `/settings` 进入控制面板进行可视化管理！"
    )


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: types.Message):
    await message.answer(
        "ℹ️ 订阅功能已全新升级，请使用 `/settings` 取消订阅！"
    )
