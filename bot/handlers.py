from __future__ import annotations
import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from config import BBDOWN_PATH, DATA_DIR, is_admin
from database import (
    add_subscription, remove_subscription, get_user_subscriptions, Subscription,
    get_videos_by_uid, count_videos_by_uid, get_unparsed_videos,
)
from bilibili_api import get_up_info, get_up_videos
from bbdown_fetcher import fetch_all_video_urls, parse_pending_videos
from subprocess_executor import (
    SubprocessExecutor, run_bbdown, run_bbdown_simple,
    DEFAULT_DOWNLOAD_TIMEOUT, DEFAULT_INFO_TIMEOUT, create_progress_bar
)

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(lambda msg: msg.from_user is not None and is_admin(msg.from_user.id))

URL_PATTERN = re.compile(r"(https?://(www\.)?(bilibili\.com|b23\.tv)/[^\s]+)")

# --- FSM States ---
class DownloadFSM(StatesGroup):
    waiting_for_pages = State()

class DownloadSession(StatesGroup):
    """用于存储用户当前正在配置的下载任务上下文"""
    waiting_for_quality = State()  # 等待选择画质
    waiting_for_pages = State()    # 等待输入分P范围

class SubFSM(StatesGroup):
    waiting_for_uid = State()
    waiting_for_keywords = State()
    waiting_for_edit_keywords = State()

# ----------------------------
# 1. Main Commands
# ----------------------------
@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 **BBDown Telegram Bot 使用帮助**\n\n"
        "**1. 账号登录（必须）**\n"
        "发送 `/settings` 进入设置菜单 -> 登录管理 -> 触发登录验证。请使用 B站 App 扫描弹出的二维码登录。\n\n"
        "**2. 如何下载视频？**\n"
        "- **直接发送链接**：将 B站视频链接（支持 b23.tv）直接发给机器人。\n"
        "- **使用命令**：或发送 `/url` 后输入。\n\n"
        "**3. 自动订阅 UP 主管理**\n"
        "发送 `/settings` 进入设置菜单 -> 订阅管理：\n"
        "- 支持多关键词过滤（逗号分隔）、自动获取最新视频补发、免打扰后台静默 30 分钟轮询。\n\n"
        "💡 *本机器人基于开源项目 [BBDown](https://github.com/nilaoda/BBDown) 驱动。*"
    )
    await message.answer(help_text, parse_mode="Markdown", disable_web_page_preview=True)

@router.message(Command("url"))
async def cmd_url(message: types.Message):
    await message.answer("🔗 请直接发送你需要下载的 Bilibili 视频链接，例如：\n`https://www.bilibili.com/video/BV1xx411c7mD`\n或分享短链 `https://b23.tv/...`", parse_mode="Markdown")

# Legacy commands gracefully redirected
@router.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message):
    await message.answer("ℹ️ 订阅功能已全新升级，请使用 `/settings` 进入控制面板进行可视化管理！")

@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: types.Message):
    await message.answer("ℹ️ 订阅功能已全新升级，请使用 `/settings` 取消订阅！")

# ----------------------------
# 2. Settings & Subscription Menus
# ----------------------------
def get_settings_main_kb():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔑 登录管理", callback_data="set_login_menu"))
    builder.row(InlineKeyboardButton(text="📋 订阅管理", callback_data="set_subs_list"))
    builder.row(InlineKeyboardButton(text="❌ 关闭菜单", callback_data="close_menu"))
    return builder.as_markup()

@router.message(Command("settings"))
async def cmd_settings(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("⚙️ **机器人控制面板**\n请选择你需要管理的功能：", reply_markup=get_settings_main_kb(), parse_mode="Markdown")

@router.callback_query(F.data == "close_menu")
async def cb_close_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()

@router.callback_query(F.data == "settings_main")
async def cb_settings_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("⚙️ **机器人控制面板**\n请选择你需要管理的功能：", reply_markup=get_settings_main_kb(), parse_mode="Markdown")

# --- Login Menu ---
@router.callback_query(F.data == "set_login_menu")
async def cb_login_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 查看登录状态 (检查失效)", callback_data="set_login_check"))
    builder.row(InlineKeyboardButton(text="📱 发起扫码登录 (生成QR)", callback_data="set_login_trigger"))
    builder.row(InlineKeyboardButton(text="🔙 返回主菜单", callback_data="settings_main"))
    await callback.message.edit_text("🔑 **登录管理**\n在此管理你的 Bilibili 登录凭证。", reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data == "set_login_check")
async def cb_login_check(callback: types.CallbackQuery):
    await callback.answer("正在检查凭证有效性，请稍候...", show_alert=False)
    # Check by running BBDown on a mock video, which is very fast and non-blocking
    result = await run_bbdown_simple(["BV1xx411c7mD", "--only-show-info"], DATA_DIR, timeout=30)
    
    if result.timed_out:
        status = f"❌ **验证超时**（30秒），BBDown 可能卡死。请重启机器人或重新扫码登录。"
    elif result.error:
        status = f"❌ **验证时发生系统错误**：{result.error}"
    elif "尚未登录" in result.output or "需扫码" in result.output or "失效" in result.output or "过期" in result.output:
        status = "❌ **登录凭证已失效或未登录**，请点击 [发起扫码登录]重新认证。"
    else:
        status = "✅ **当前处于已登录状态**，您的认证凭证完全有效！"
        
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 返回登录菜单", callback_data="set_login_menu"))
    await callback.message.edit_text(f"🔑 **登录状态检查报告**\n\n{status}", reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data == "set_login_trigger")
async def cb_login_trigger(callback: types.CallbackQuery):
    await callback.answer("请发送 /login 到聊天框即可启动二维码生成。", show_alert=True)

# --- Subscriptions List ---
@router.callback_query(F.data == "set_subs_list")
async def cb_subs_list(callback: types.CallbackQuery):
    subs = await get_user_subscriptions(callback.from_user.id)
    
    builder = InlineKeyboardBuilder()
    if subs:
        for sub in subs:
            name_disp = sub.up_name if sub.up_name else f"UID:{sub.uid}"
            builder.row(InlineKeyboardButton(text=f"👤 {name_disp}", callback_data=f"sub_detail_{sub.uid}"))
            
    builder.row(InlineKeyboardButton(text="➕ 添加新订阅 (Add UP)", callback_data="sub_add"))
    builder.row(InlineKeyboardButton(text="🔙 返回主菜单", callback_data="settings_main"))
    
    text = f"📋 **订阅管理**\n您当前共有 **{len(subs)}** 个活跃订阅。\n请点击 UP 主名字进行详细配置，或点击下方按钮添加。"
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

# --- Add Subscription Flow ---
@router.callback_query(F.data == "sub_add")
async def cb_sub_add(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SubFSM.waiting_for_uid)
    builder = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 取消并返回", callback_data="set_subs_list"))
    await callback.message.edit_text("➕ **添加新订阅**\n\n请**回复本消息**输入你要订阅的 UP 主 **UID**(纯数字)：", reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.message(SubFSM.waiting_for_uid)
async def process_sub_uid(message: types.Message, state: FSMContext):
    uid = message.text.strip()
    if not uid.isdigit():
        builder = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙取消", callback_data="set_subs_list"))
        await message.answer("❌ UID 必须是纯数字。请重新发送:", reply_markup=builder.as_markup())
        return
        
    # fetch UP info
    processing_msg = await message.answer("🔍 正在拉取 UP 主信息...")
    up_info = await get_up_info(uid)
    up_name = up_info["name"] if up_info else "Unknown UP"
    
    await state.update_data(uid=uid, up_name=up_name)
    await state.set_state(SubFSM.waiting_for_keywords)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⏭️ 跳过 (无关键词，全部下载)", callback_data="sub_add_skip_kw"))
    builder.row(InlineKeyboardButton(text="🔙 取消", callback_data="set_subs_list"))
    
    await processing_msg.edit_text(
        f"✅ 已识别 UP 主：**{up_name}** (`{uid}`)\n\n"
        "请发送您要过滤的**标题关键词**（支持多个，请用逗号分隔，例如：`Vlog,日常,测评`）。\n"
        "只有标题包含这些关键词时，机器人才会自动推送。\n\n"
        "如果您想下载Ta发布的所有视频，请点击下方跳过。",
        reply_markup=builder.as_markup(), parse_mode="Markdown"
    )

@router.callback_query(F.data == "sub_add_skip_kw")
async def cb_sub_add_skip_kw(callback: types.CallbackQuery, state: FSMContext):
    await finish_add_sub(callback.message, state, None)

@router.message(SubFSM.waiting_for_keywords)
async def process_sub_keywords(message: types.Message, state: FSMContext):
    await finish_add_sub(message, state, message.text.strip())

async def finish_add_sub(msg_obj: types.Message, state: FSMContext, keywords: str):
    data = await state.get_data()
    uid = data["uid"]
    up_name = data["up_name"]
    
    success = await add_subscription(uid, msg_obj.chat.id, keywords, up_name)
    await state.clear()
    
    if success:
        await msg_obj.answer(f"🎉 **订阅成功！**\n👤 UP: {up_name}\n🏷️ 关键词: {keywords if keywords else '全部无过滤'}", parse_mode="Markdown")
        # Prompt video page 1 directly
        await show_up_videos_gui(msg_obj, uid, up_name, 1)
    else:
        builder = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 返回列表", callback_data="set_subs_list"))
        await msg_obj.answer("❌ 订阅失败，您可能已经在此对话中订阅过该 UP 主。", reply_markup=builder.as_markup())

# --- Subscription Details ---
@router.callback_query(F.data.startswith("sub_detail_"))
async def cb_sub_detail(callback: types.CallbackQuery):
    uid = callback.data.replace("sub_detail_", "")
    subs = await get_user_subscriptions(callback.from_user.id)
    sub = next((s for s in subs if s.uid == uid), None)
    if not sub:
        await callback.answer("未找到该订阅", show_alert=True)
        return await cb_subs_list(callback)

    name_disp = sub.up_name if sub.up_name else "Unknown"
    kw_disp = sub.keyword if sub.keyword else "全部内容 (无过滤)"

    # Check local BBDown cache
    cached_count = await count_videos_by_uid(uid)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📺 查看近期视频 (Bilibili API)", callback_data=f"sub_v_p_{uid}_1"))
    if cached_count > 0:
        builder.row(InlineKeyboardButton(
            text=f"🗂️ 浏览本地视频列表 ({cached_count} 个)",
            callback_data=f"sub_v_full_{uid}_1"
        ))
    builder.row(InlineKeyboardButton(
        text="📦 用 BBDown 抓取全部视频列表",
        callback_data=f"sub_fetch_full_{uid}"
    ))
    builder.row(InlineKeyboardButton(text="📝 修改关键词过滤", callback_data=f"sub_editkw_{uid}"))
    builder.row(InlineKeyboardButton(text="🗑️ 删除此订阅", callback_data=f"sub_del_{uid}"))
    builder.row(InlineKeyboardButton(text="🔙 返回订阅列表", callback_data="set_subs_list"))

    cache_note = f"\n📦 本地已缓存 **{cached_count}** 个视频" if cached_count > 0 else "\n📦 尚未抓取本地缓存"
    text = (
        f"👤 **订阅详情**\n\n"
        f"**UP主**: {name_disp}\n"
        f"**UID**: `{uid}`\n"
        f"**触发关键词**: {kw_disp}"
        f"{cache_note}\n\n"
        f"*机器每 30 分钟后台轮询一次*"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("sub_del_"))
async def cb_sub_del(callback: types.CallbackQuery):
    uid = callback.data.replace("sub_del_", "")
    await remove_subscription(uid, callback.from_user.id)
    await callback.answer("✅ 已删除该订阅！")
    await cb_subs_list(callback)

@router.callback_query(F.data.startswith("sub_editkw_"))
async def cb_sub_editkw(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.data.replace("sub_editkw_", "")
    await state.update_data(edit_uid=uid)
    await state.set_state(SubFSM.waiting_for_edit_keywords)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🚫 清空专属关键词 (全部下载)", callback_data=f"sub_doeditkw_{uid}_CLEAR"))
    builder.row(InlineKeyboardButton(text="🔙 取消返回", callback_data=f"sub_detail_{uid}"))
    
    await callback.message.edit_text(
        f"📝 **修改关键字** (UID: `{uid}`)\n"
        "请直接回复新关键字（多个请用逗号分隔）。",
        reply_markup=builder.as_markup(), parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("sub_doeditkw_"))
async def cb_sub_doeditkw(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    uid = parts[2]
    await add_subscription(uid, callback.from_user.id, None, None) # will update because exist
    await state.clear()
    await callback.answer("✅ 已清空过滤词，今后该 UP 的所有新视频皆会收到通知！", show_alert=True)
    # Re-trigger detail view trick
    callback.data = f"sub_detail_{uid}"
    await cb_sub_detail(callback)

@router.message(SubFSM.waiting_for_edit_keywords)
async def process_sub_editkw(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data["edit_uid"]
    kw = message.text.strip()
    
    await add_subscription(uid, message.chat.id, kw, None)
    await state.clear()
    
    builder = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 查看该订阅", callback_data=f"sub_detail_{uid}"))
    await message.answer(f"✅ 更新成功！当前触发关键词： {kw}", reply_markup=builder.as_markup())

# --- Full Video List (BBDown local cache) ---

async def show_full_video_list(
    msg_obj: types.Message,
    uid: str,
    up_name: str,
    page: int,
    is_edit: bool = False,
):
    """Display paginated video list from the local up_videos DB cache."""
    PAGE_SIZE = 8
    videos = await get_videos_by_uid(uid, page=page, page_size=PAGE_SIZE)
    total = await count_videos_by_uid(uid)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    builder = InlineKeyboardBuilder()
    if not videos:
        txt = (
            f"🗂️ **{up_name} 本地视频列表** (第 {page} 页)\n\n"
            f"❌ 此页暂无数据，请先点击「抓取全部视频列表」按鈕。"
        )
    else:
        global_start = (page - 1) * PAGE_SIZE + 1
        title_lines = ""
        for idx, v in enumerate(videos):
            seq = global_start + idx
            display_title = v.title if v.title else f"[待解析] {v.bvid}"
            title_lines += f"`{seq}.` {display_title}\n"
            short = display_title[:22] + ("…" if len(display_title) > 22 else "")
            builder.row(InlineKeyboardButton(
                text=f"{seq}. {short}",
                callback_data=f"directdl_{v.bvid}"
            ))
        txt = (
            f"🗂️ **{up_name} 的全部投稿** (第 {page}/{total_pages} 页，共 {total} 个)\n"
            f"*点击视频序号按钮即可开始下载*\n\n"
            f"{title_lines}"
        )

    # Navigation row
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ 上一页", callback_data=f"sub_v_full_{uid}_{page - 1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="下一页 ➡️", callback_data=f"sub_v_full_{uid}_{page + 1}"))
    if nav_row:
        builder.row(*nav_row)
    builder.row(InlineKeyboardButton(text="📦 重新抓取/刷新列表", callback_data=f"sub_fetch_full_{uid}"))
    builder.row(InlineKeyboardButton(text="🔙 返回订阅详情", callback_data=f"sub_detail_{uid}"))

    if is_edit:
        await msg_obj.edit_text(txt, reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await msg_obj.answer(txt, reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("sub_v_full_"))
async def cb_sub_v_full(callback: types.CallbackQuery):
    """Pagination handler for the local (BBDown-cached) full video list."""
    # Format: sub_v_full_{uid}_{page}
    parts = callback.data.split("_")
    uid = parts[3]
    page = int(parts[4])

    subs = await get_user_subscriptions(callback.from_user.id)
    sub = next((s for s in subs if s.uid == uid), None)
    up_name = sub.up_name if sub and sub.up_name else f"UID {uid}"

    await callback.answer()
    await show_full_video_list(callback.message, uid, up_name, page, is_edit=True)


@router.callback_query(F.data.startswith("sub_fetch_full_"))
async def cb_sub_fetch_full(callback: types.CallbackQuery):
    """Trigger BBDown to fetch all video URLs then parse titles for a UP master."""
    uid = callback.data.replace("sub_fetch_full_", "")

    subs = await get_user_subscriptions(callback.from_user.id)
    sub = next((s for s in subs if s.uid == uid), None)
    up_name = sub.up_name if sub and sub.up_name else f"UID {uid}"

    await callback.answer("开始后台抓取，请稍候...", show_alert=False)
    status_msg = await callback.message.answer(
        f"📦 **正在用 BBDown 扫描 {up_name} 的全部投稿视频**\n"
        f"UID: `{uid}`\n\n"
        f"⏳ 正在枚举视频 URL，时间取决于视频数量，请耐心等待…",
        parse_mode="Markdown"
    )

    # Step 1: Fetch all URLs via BBDown -po -p ALL
    async def url_status(msg: str):
        try:
            await status_msg.edit_text(msg, parse_mode="Markdown")
        except Exception:
            pass

    new_count = await fetch_all_video_urls(uid, status_callback=url_status)

    # Step 2: Parse pending titles
    unparsed = await get_unparsed_videos(uid, limit=500)
    pending_total = len(unparsed)

    if pending_total > 0:
        try:
            await status_msg.edit_text(
                f"✅ URL 枚举完毕，新增 **{new_count}** 条。\n"
                f"⚙️ 开始解析视频标题（共 **{pending_total}** 条待解析），每条约需 2-5 秒…",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        async def parse_status(done: int, total: int):
            pct = done * 100 // total
            bar_filled = int(done * 20 // total)
            bar = "▓" * bar_filled + "░" * (20 - bar_filled)
            try:
                await status_msg.edit_text(
                    f"⚙️ **正在解析视频标题**\n"
                    f"进度: `{done}/{total}` 条\n"
                    f"`{bar}` {pct}%",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        parsed = await parse_pending_videos(uid, status_callback=parse_status)

        try:
            await status_msg.edit_text(
                f"🎉 **抓取解析完毕！**\n"
                f"新增 URL: **{new_count}** 条 | 成功解析标题: **{parsed}** 条\n\n"
                f"正在载入视频列表…",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    else:
        try:
            await status_msg.edit_text(
                f"✅ **扫描完毕！**\n新增 **{new_count}** 条 URL，所有视频均已解析。\n\n正在载入视频列表…",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    # Show the paginated list
    await show_full_video_list(status_msg, uid, up_name, page=1, is_edit=True)


# --- UP Video Pagination Flow (Bilibili API) ---
async def show_up_videos_gui(msg_obj: types.Message, uid: str, up_name: str, page: int, is_edit: bool = False):
    loading_text = f"🔄 正在向 B 站请求 {up_name} 的第 {page} 页视频..."
    if is_edit:
        await msg_obj.edit_text(loading_text)
    else:
        msg_obj = await msg_obj.answer(loading_text)
        
    videos = await get_up_videos(uid, pn=page, ps=10)
    
    builder = InlineKeyboardBuilder()
    if not videos:
        txt = f"📺 **{up_name} 的投稿视频** (第 {page} 页)\n\n❌ 抱歉，获取失败或此页已无更多内容返回。"
    else:
        txt = f"📺 **{up_name} 的投稿视频** (第 {page} 页)\n*点击下方对应序号的按钮，机器人将立即开始下载并发送给你！*\n\n"
        for idx, v in enumerate(videos, 1):
            txt += f"`{idx}.` {v['title']}\n"
            # Limit callback data length, pass download command
            builder.row(InlineKeyboardButton(text=f"📥 下载第 {idx} 个: {v['title'][:12]}...", callback_data=f"directdl_{v['bvid']}"))
            
    # Nav buttons
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ 上一页", callback_data=f"sub_v_p_{uid}_{page - 1}"))
    if videos and len(videos) == 10:
        nav_row.append(InlineKeyboardButton(text="下一页 ➡️", callback_data=f"sub_v_p_{uid}_{page + 1}"))
        
    if nav_row:
        builder.row(*nav_row)
        
    builder.row(InlineKeyboardButton(text="🔙 返回订阅详情", callback_data=f"sub_detail_{uid}"))
    await msg_obj.edit_text(txt, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("sub_v_p_"))
async def cb_sub_v_p(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    uid = parts[3]
    page = int(parts[4])
    
    subs = await get_user_subscriptions(callback.from_user.id)
    sub = next((s for s in subs if s.uid == uid), None)
    up_name = sub.up_name if sub and sub.up_name else f"UID {uid}"
    
    await show_up_videos_gui(callback.message, uid, up_name, page, is_edit=True)

# ----------------------------
# 3. Download Trigger & Flow
# ----------------------------
@router.callback_query(F.data.startswith("directdl_"))
async def cb_directdl(callback: types.CallbackQuery, state: FSMContext):
    bvid = callback.data.replace("directdl_", "")
    await callback.answer("任务已安排！", show_alert=False)
    url = f"https://www.bilibili.com/video/{bvid}"
    # 使用 FSMContext 避免竞态条件
    await trigger_download_selection(callback.message, state, url)

async def trigger_download_selection(message: types.Message, state: FSMContext, url: str):
    """解析视频并让用户选择格式/分P"""
    await state.clear()
    
    status_msg = await message.answer(f"🔍 解析视频: `{url}`...", parse_mode="Markdown")
    info = await get_video_info(url)
    if not info:
        await status_msg.edit_text("❌ 解析失败，请确认该视频公开可见。")
        return
        
    # 使用 FSMContext 存储会话状态，避免全局字典的竞态条件
    await state.update_data(
        url=url,
        title=info["title"],
        total_pages=info["total_pages"]
    )
    
    parts = info.get("parts", [])
    if parts and len(parts) > 1:
        chunk_text = f"**{info['title']}**\n(Total Pages: {info['total_pages']})\n\n**Playlist Parts:**\n"
        is_first_chunk = True
        for p in parts:
            line = f"`{p['index']:03d}` - {p['title']}\n"
            if len(chunk_text) + len(line) > 3800:
                if is_first_chunk:
                    await status_msg.edit_text(chunk_text, parse_mode="Markdown")
                    is_first_chunk = False
                else:
                    await message.answer(chunk_text, parse_mode="Markdown")
                chunk_text = ""
            chunk_text += line
        if chunk_text:
            if is_first_chunk: await status_msg.edit_text(chunk_text, parse_mode="Markdown")
            else: await message.answer(chunk_text, parse_mode="Markdown")
        action_msg_text = "📺 这是一个多 P 播放列表，请选择下载格式："
    else:
        await status_msg.edit_text(f"📺 **{info['title']}**\n(Total Pages: 1)")
        action_msg_text = "请选择你要提取的格式："
        
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎬 最高画质直出 (默认推荐)", callback_data="dlq_best"))
    builder.row(InlineKeyboardButton(text="🎵 仅提取音频 (MP3/M4A)", callback_data="dlq_audio"))
    builder.row(InlineKeyboardButton(text="📺 单独提取弹幕文件", callback_data="dlq_danmaku"))
    
    await message.answer(action_msg_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.message(F.text, F.text.regexp(URL_PATTERN))
async def handle_bilibili_link(message: types.Message, state: FSMContext):
    await state.clear()
    match = URL_PATTERN.search(message.text)
    if not match: return
    await trigger_download_selection(message, state, match.group(1))

async def get_video_info(url: str) -> Optional[dict]:
    result = await run_bbdown_simple([url, "--only-show-info", "--show-all"], DATA_DIR, timeout=DEFAULT_INFO_TIMEOUT)
    if result.return_code != 0:
        return None
    
    title = "Unknown Title"
    qualities = []
    total_pages = 1
    parts = []
    
    for line in result.output.split('\n'):
        if "视频标题:" in line: title = line.split("视频标题:", 1)[1].strip()
        if "个分P" in line:
            m = re.search(r'(\d+)\s*个分P', line)
            if m: total_pages = int(m.group(1))
        match = re.search(r"^\s*(\d+)\.\s*(.*?)$", line)
        if match and "画质代码:" not in line: qualities.append({"id": match.group(1), "name": match.group(2).strip()})
        part_match = re.search(r"-\s*P(\d+):\s*\[([^\]]+)\]\s*\[(.*)\]\s*\[([^\]]+)\]", line)
        if part_match: parts.append({"index": int(part_match.group(1)), "title": part_match.group(3).strip()})
            
    return {"title": title, "qualities": qualities, "total_pages": total_pages, "parts": parts}

# ----------------------------
# 4. Old Flow Handlers (Selections)
# ----------------------------
@router.callback_query(F.data.startswith("dlq_"))
async def handle_quality_selection(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data or "url" not in data:
        return await callback.answer("会话已过期，请重新发送视频链接。", show_alert=True)
    
    action = callback.data.replace("dlq_", "")
    # 将画质选项存入 FSM
    await state.update_data(action=action)
    
    total_pages = data.get("total_pages", 1)
    
    builder = InlineKeyboardBuilder()
    if total_pages > 1:
        builder.row(InlineKeyboardButton(text="📥 下载所有 P (批量)", callback_data="dlp_all"))
        builder.row(InlineKeyboardButton(text="🔽 仅下载 P1", callback_data="dlp_1"))
        builder.row(InlineKeyboardButton(text="✏️ 自定义 P 数范围", callback_data="dlp_custom"))
        await callback.message.edit_text(f"**这是一个合集视频** (总共 {total_pages} P)。\n您希望下载哪些章节？", reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        builder.row(InlineKeyboardButton(text="🚀 开始下载并上传", callback_data="dlp_1"))
        await callback.message.edit_text(f"配置完毕，**准备就绪**。\n点击启动后将在后台处理，请耐心等待文件回传。", reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("dlp_"))
async def handle_page_selection(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data or "url" not in data:
        return await callback.answer("会话已过期，请重新发送视频链接。", show_alert=True)
    
    page_action = callback.data.replace("dlp_", "")
    total_pages = data.get("total_pages", 1)
    
    if page_action == "custom":
        await state.set_state(DownloadSession.waiting_for_pages)
        await callback.message.edit_text("✏️ **请求自定义输入页数：**\n\n请直接回复本条，输入你想下载的页面。\n举个例子：\n`1-3`（代表下载 1,2,3 分P）\n`1,4,7`（代表离散下载）", parse_mode="Markdown")
        return
    elif page_action == "all": pages = list(range(1, total_pages + 1))
    elif page_action == "1": pages = [1]
    else: pages = [1]
    
    # 从 FSM 获取完整 session 数据并开始下载
    await start_multi_download(callback.message, data, pages)

@router.message(DownloadSession.waiting_for_pages)
async def process_custom_pages(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if not data or "url" not in data:
        await state.clear()
        return await message.answer("会话已过期，请重新发送视频链接。")
        
    text = message.text.replace(" ", "").replace("，", ",")
    total_pages = data.get("total_pages", 1)
    pages = []
    
    try:
        parts = text.split(',')
        for part in parts:
            if not part: continue
            if '-' in part:
                start, end = map(int, part.split('-'))
                start = max(1, start); end = min(total_pages, end)
                if start <= end: pages.extend(range(start, end + 1))
            else:
                p = int(part)
                if 1 <= p <= total_pages: pages.append(p)
        pages = sorted(list(set(pages)))
        if not pages: raise ValueError("No valid pages found")
    except Exception:
        return await message.answer("❌ 格式错误！请重试或者发送新的网址取消操作。正确格式例如: `1-5,7`", parse_mode="Markdown")
        
    await state.clear()
    status_msg = await message.answer("✅ 自定义页数锁定，开始提取队列...")
    await start_multi_download(status_msg, data, pages)

async def start_multi_download(status_msg: types.Message, session: dict, pages: list[int]):
    """开始多P下载任务 - 使用统一的 SubprocessExecutor"""
    if not session or "url" not in session:
        return await status_msg.edit_text("会话数据无效。")
        
    url = session["url"]
    action = session.get("action", "best")
    title = session.get("title", "Unknown")
    
    cmd_args = [url]
    if action == "audio": cmd_args.append("--audio-only")
    elif action == "danmaku": cmd_args.append("--danmaku")
    elif action == "sub": cmd_args.append("--sub-only")
    
    # 使用 URL hash 作为下载目录标识
    import hashlib
    dl_id = hashlib.md5(url.encode()).hexdigest()[:8]
    dl_dir = Path(DATA_DIR) / "downloads" / dl_id
    dl_dir.mkdir(parents=True, exist_ok=True)
    
    await status_msg.edit_text(f"🚀 已排队 {len(pages)} 个任务。\n当前: P{pages[0]}")
    
    for i, p in enumerate(pages):
        current_cmd_args = cmd_args.copy()
        current_cmd_args.extend(["-p", str(p), "--work-dir", str(dl_dir.absolute())])
        
        try: 
            await status_msg.edit_text(f"📥 **拉取数据库 P{p}** ({i+1}/{len(pages)})...\n标题: `{title}`", parse_mode="Markdown")
        except: pass
        
        # 使用统一的 SubprocessExecutor
        executor = SubprocessExecutor(timeout=DEFAULT_DOWNLOAD_TIMEOUT)
        
        last_update_time = time.time()
        last_percentage = 0.0
        current_text = ""
        
        async def flush_ui(text: str, force: bool = False):
            nonlocal last_update_time, current_text
            current_time = time.time()
            if force or (current_time - last_update_time) >= 3.0:
                full_text = f"📥 **正在进行任务 P{p}** ({i+1}/{len(pages)})\n{text}"
                if full_text != current_text:
                    try:
                        await status_msg.edit_text(full_text, parse_mode="Markdown")
                        current_text = full_text
                        last_update_time = current_time
                    except Exception: pass

        try:
            async for progress in executor.run_with_progress([BBDOWN_PATH] + current_cmd_args, DATA_DIR):
                if abs(progress.percentage - last_percentage) >= 5.0 or (time.time() - last_update_time) >= 3.0:
                    bar = create_progress_bar(progress.percentage)
                    await flush_ui(f"`{bar}`", force=True)
                    last_percentage = progress.percentage
                elif progress.percentage == 100.0:
                    await flush_ui(f"🔄 **打包编码封装中，请等候...**", force=True)
            
            result = await executor.wait()
            
        except Exception as e:
            logger.error(f"Error during P{p} download: {e}")
            await executor.kill()
            await state.clear() if hasattr(state, 'clear') else None
            await status_msg.answer(f"❌ P{p} 下载出错: {e}")
            continue
        
        if result.timed_out:
            await status_msg.answer(f"❌ **下载超时，已强制终止任务 (超时 {DEFAULT_DOWNLOAD_TIMEOUT//60} 分钟)**。", parse_mode="Markdown")
            for f in dl_dir.glob("*"):
                try: os.remove(f)
                except: pass
            continue
            
        if result.return_code != 0:
            await status_msg.answer(f"❌ 下载 P{p} 失败, 错误代码 {result.return_code}。")
            for f in dl_dir.glob("*"):
                try: os.remove(f)
                except: pass
            continue

        await flush_ui("☁️ **准备向 Telegram Cloud 上传结果...**", force=True)
        await asyncio.sleep(1.5)
        
        downloaded_files = [f for f in dl_dir.rglob("*") if f.is_file() and f.suffix.lower() not in ['.jpg', '.png']]
        if not downloaded_files:
            await status_msg.answer(f"❌ 查无打包文件。可能 P{p} 已受版权封存导致无文件导出。")
            try: shutil.rmtree(dl_dir)
            except: pass
            continue
            
        target_file = max(downloaded_files, key=lambda f: f.stat().st_size)
        try:
            file = FSInputFile(str(target_file))
            file_cap = f"{title} (P{p})"
            if target_file.suffix.lower() == ".mp4": await status_msg.answer_video(file, caption=file_cap)
            elif target_file.suffix.lower() in [".mp3", ".m4a", ".aac"]: await status_msg.answer_audio(file, caption=file_cap)
            else: await status_msg.answer_document(file, caption=file_cap)
            await flush_ui(f"✅ **P{p} 传输完毕！**", force=True)
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
            await status_msg.answer(f"❌ 推送限制引发失败或阻断： {e}")
        finally:
            try: shutil.rmtree(dl_dir)
            except: pass
                    
