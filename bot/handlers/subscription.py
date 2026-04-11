"""
bot/handlers/subscription.py - 订阅管理处理

包含：
- 订阅列表
- 添加/删除订阅
- 关键词管理
- 视频列表浏览
"""

import asyncio
import logging

from aiogram import Router, types, F
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from bot.config import is_admin
from bot.database import (
    add_subscription, remove_subscription, get_user_subscriptions,
    get_videos_by_uid, count_videos_by_uid, get_unparsed_videos,
)
from bot.bilibili_api import get_up_info, get_up_videos
from bot.bbdown_fetcher import fetch_all_video_urls, parse_pending_videos
from bot.utils import escape_markdown

# 防止 Python 3.11+ 提前 GC 回收 fire-and-forget 任务（F-05 修复）
# key=uid 保证：1) 引用不丢失 2) 同一 uid 不会并发两个任务 3) 完成后自动清理
_background_parse_tasks: dict[str, asyncio.Task] = {}

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(lambda msg: msg.from_user is not None and is_admin(msg.from_user.id))
router.callback_query.filter(lambda c: c.from_user is not None and is_admin(c.from_user.id))


class SubFSM(StatesGroup):
    """订阅管理的 FSM 状态"""
    waiting_for_uid = State()
    waiting_for_keywords = State()
    waiting_for_edit_keywords = State()


# --- Subscriptions List ---
@router.callback_query(F.data == "set_subs_list")
async def cb_subs_list(callback: types.CallbackQuery):
    """显示订阅列表"""
    subs = await get_user_subscriptions(callback.from_user.id)
    
    builder = InlineKeyboardBuilder()
    if subs:
        for sub in subs:
            name_disp = sub.up_name if sub.up_name else f"UID:{sub.uid}"
            builder.row(InlineKeyboardButton(
                text=f"👤 {name_disp}",
                callback_data=f"sub_detail_{sub.uid}"
            ))
            
    builder.row(InlineKeyboardButton(
        text="➕ 添加新订阅 (Add UP)",
        callback_data="sub_add"
    ))
    builder.row(InlineKeyboardButton(
        text="🔙 返回主菜单",
        callback_data="settings_main"
    ))
    
    text = (
        f"📋 **订阅管理**\n"
        f"您当前共有 **{len(subs)}** 个活跃订阅。\n"
        f"请点击 UP 主名字进行详细配置，或点击下方按钮添加。"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


# --- Add Subscription Flow ---
@router.callback_query(F.data == "sub_add")
async def cb_sub_add(callback: types.CallbackQuery, state: FSMContext):
    """开始添加订阅"""
    await state.set_state(SubFSM.waiting_for_uid)
    builder = InlineKeyboardBuilder().row(
        InlineKeyboardButton(text="🔙 取消并返回", callback_data="set_subs_list")
    )
    await callback.message.edit_text(
        "➕ **添加新订阅**\n\n"
        "请**回复本消息**输入你要订阅的 UP 主 **UID**(纯数字)：",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.message(SubFSM.waiting_for_uid)
async def process_sub_uid(message: types.Message, state: FSMContext):
    """处理 UID 输入"""
    uid = message.text.strip()
    if not uid.isdigit():
        builder = InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="🔙取消", callback_data="set_subs_list")
        )
        await message.answer("❌ UID 必须是纯数字。请重新发送:", reply_markup=builder.as_markup())
        return
        
    # fetch UP info
    processing_msg = await message.answer("🔍 正在拉取 UP 主信息...")
    up_info = await get_up_info(uid)
    up_name = up_info["name"] if up_info else "Unknown UP"
    
    await state.update_data(uid=uid, up_name=up_name)
    await state.set_state(SubFSM.waiting_for_keywords)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="⏭️ 跳过 (无关键词，全部下载)",
        callback_data="sub_add_skip_kw"
    ))
    builder.row(InlineKeyboardButton(text="🔙 取消", callback_data="set_subs_list"))
    
    await processing_msg.edit_text(
        f"✅ 已识别 UP 主：**{escape_markdown(up_name)}** (`{uid}`)\n\n"
        "请发送您要过滤的**标题关键词**（支持多个，请用逗号分隔，例如：`Vlog,日常,测评`）。\n"
        "只有标题包含这些关键词时，机器人才会自动推送。\n\n"
        "如果您想下载Ta发布的所有视频，请点击下方跳过。",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "sub_add_skip_kw")
async def cb_sub_add_skip_kw(callback: types.CallbackQuery, state: FSMContext):
    """跳过关键词"""
    await finish_add_sub(callback.message, state, None)


@router.message(SubFSM.waiting_for_keywords)
async def process_sub_keywords(message: types.Message, state: FSMContext):
    """处理关键词输入"""
    await finish_add_sub(message, state, message.text.strip())


async def finish_add_sub(msg_obj: types.Message, state: FSMContext, keywords: str):
    """完成订阅添加"""
    data = await state.get_data()
    uid = data["uid"]
    up_name = data["up_name"]
    
    success = await add_subscription(uid, msg_obj.chat.id, keywords, up_name)
    await state.clear()
    
    if success:
        await msg_obj.answer(
            f"🎉 **订阅成功！**\n👤 UP: {escape_markdown(up_name)}\n🏷️ 关键词: {keywords if keywords else '全部无过滤'}",
            parse_mode="Markdown"
        )
        # Prompt video page 1 directly
        await show_up_videos_gui(msg_obj, uid, up_name, 1)
    else:
        await msg_obj.answer(
            f"✅ **订阅已更新！**\n👤 UP: {escape_markdown(up_name)}\n🏷️ 关键词: {keywords if keywords else '全部无过滤'}\n\n"
            "该 UP 已存在，关键词已更新。",
            parse_mode="Markdown"
        )


# --- Subscription Details ---
@router.callback_query(F.data.startswith("sub_detail_"))
async def cb_sub_detail(callback: types.CallbackQuery):
    """显示订阅详情"""
    uid = callback.data.replace("sub_detail_", "")
    subs = await get_user_subscriptions(callback.from_user.id)
    sub = next((s for s in subs if s.uid == uid), None)
    if not sub:
        await callback.answer("未找到该订阅", show_alert=True)
        # 重新显示列表
        return await cb_subs_list(callback)

    name_disp = sub.up_name if sub.up_name else "Unknown"
    kw_disp = sub.keyword if sub.keyword else "全部内容 (无过滤)"

    # Check local BBDown cache
    cached_count = await count_videos_by_uid(uid)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="📺 查看近期视频 (Bilibili API)",
        callback_data=f"sub_v_p_{uid}_1"
    ))
    if cached_count > 0:
        builder.row(InlineKeyboardButton(
            text=f"🗂️ 浏览本地视频列表 ({cached_count} 个)",
            callback_data=f"sub_v_full_{uid}_1"
        ))
    builder.row(InlineKeyboardButton(
        text="📦 用 BBDown 抓取全部视频列表",
        callback_data=f"sub_fetch_full_{uid}"
    ))
    builder.row(InlineKeyboardButton(
        text="📝 修改关键词过滤",
        callback_data=f"sub_editkw_{uid}"
    ))
    builder.row(InlineKeyboardButton(
        text="🗑️ 删除此订阅",
        callback_data=f"sub_del_{uid}"
    ))
    builder.row(InlineKeyboardButton(
        text="🔙 返回订阅列表",
        callback_data="set_subs_list"
    ))

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
    """删除订阅"""
    uid = callback.data.replace("sub_del_", "")
    await remove_subscription(uid, callback.from_user.id)
    await callback.answer("✅ 已删除该订阅！")
    await cb_subs_list(callback)


@router.callback_query(F.data.startswith("sub_editkw_"))
async def cb_sub_editkw(callback: types.CallbackQuery, state: FSMContext):
    """编辑关键词"""
    uid = callback.data.replace("sub_editkw_", "")
    await state.update_data(edit_uid=uid)
    await state.set_state(SubFSM.waiting_for_edit_keywords)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="🚫 清空专属关键词 (全部下载)",
        callback_data=f"sub_doeditkw_{uid}_CLEAR"
    ))
    builder.row(InlineKeyboardButton(
        text="🔙 取消返回",
        callback_data=f"sub_detail_{uid}"
    ))
    
    await callback.message.edit_text(
        f"📝 **修改关键字** (UID: `{uid}`)\n"
        "请直接回复新关键字（多个请用逗号分隔）。",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("sub_doeditkw_"))
async def cb_sub_doeditkw(callback: types.CallbackQuery, state: FSMContext):
    """清空关键词"""
    # 格式: sub_doeditkw_{uid}_CLEAR
    uid = callback.data.removeprefix("sub_doeditkw_").removesuffix("_CLEAR")
    await add_subscription(uid, callback.from_user.id, None, None)
    await state.clear()
    await callback.answer("✅ 已清空过滤词，今后该 UP 的所有新视频皆会收到通知！", show_alert=True)
    # Re-trigger detail view
    callback.data = f"sub_detail_{uid}"
    await cb_sub_detail(callback)


@router.message(SubFSM.waiting_for_edit_keywords)
async def process_sub_editkw(message: types.Message, state: FSMContext):
    """处理关键词编辑"""
    data = await state.get_data()
    uid = data["edit_uid"]
    kw = message.text.strip()
    
    await add_subscription(uid, message.chat.id, kw, None)
    await state.clear()
    
    builder = InlineKeyboardBuilder().row(
        InlineKeyboardButton(text="🔙 查看该订阅", callback_data=f"sub_detail_{uid}")
    )
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
        nav_row.append(InlineKeyboardButton(
            text="⬅️ 上一页",
            callback_data=f"sub_v_full_{uid}_{page - 1}"
        ))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(
            text="下一页 ➡️",
            callback_data=f"sub_v_full_{uid}_{page + 1}"
        ))
    if nav_row:
        builder.row(*nav_row)
    builder.row(InlineKeyboardButton(
        text="📦 重新抓取/刷新列表",
        callback_data=f"sub_fetch_full_{uid}"
    ))
    builder.row(InlineKeyboardButton(
        text="🔙 返回订阅详情",
        callback_data=f"sub_detail_{uid}"
    ))

    if is_edit:
        await msg_obj.edit_text(txt, reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await msg_obj.answer(txt, reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("sub_v_full_"))
async def cb_sub_v_full(callback: types.CallbackQuery):
    """Pagination handler for the local (BBDown-cached) full video list."""
    # Format: sub_v_full_{uid}_{page}  → rsplit gives ['sub_v_full', uid, page]
    prefix, uid, page_str = callback.data.rsplit("_", 2)
    page = int(page_str)

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

    # Step 1: Fetch all URLs via BBDown -po -p ALL (同步，等待完成)
    async def url_status(msg: str):
        try:
            await status_msg.edit_text(msg, parse_mode="Markdown")
        except Exception:
            pass

    new_count, total_count = await fetch_all_video_urls(uid, status_callback=url_status)

    # Step 2: 检查待解析数量，立刻给用户返回进度
    unparsed = await get_unparsed_videos(uid, limit=500)
    pending_total = len(unparsed)

    if pending_total > 0:
        try:
            await status_msg.edit_text(
                f"✅ URL 枚举完毕，新增 **{new_count}** 条（共 {total_count} 条）。\n"
                f"⚙️ 开始后台解析视频标题（共 **{pending_total}** 条），将在完成后通知你…",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        # 后台任务：异步解析标题，不阻塞 Handler
        async def background_parse():
            async def parse_status(done: int, total: int):
                pct = done * 100 // total if total > 0 else 100
                bar_filled = int(done * 20 // total) if total > 0 else 20
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
                    f"请点击「浏览本地视频列表」查看全部。",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        # 保存任务引用，防止 Python 3.11+ 被 GC 提前回收导致异常静默丢失
        task = asyncio.create_task(background_parse())
        task.add_done_callback(
            lambda t: logger.error(f"Background parse task failed: {t.exception()}")
            if t.exception() else logger.info("Background parse completed successfully")
        )
        _background_parse_tasks[uid] = task

    else:
        try:
            await status_msg.edit_text(
                f"✅ **扫描完毕！**\n新增 **{new_count}** 条 URL（共 {total_count} 条），所有视频均已解析。\n\n"
                "正在载入视频列表…",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    # 立刻展示列表（URL 已完整，标题待后台补全）
    await show_full_video_list(status_msg, uid, up_name, page=1, is_edit=True)


# --- UP Video Pagination Flow (Bilibili API) ---
async def show_up_videos_gui(msg_obj: types.Message, uid: str, up_name: str, page: int, is_edit: bool = False):
    """显示 UP 主视频列表（通过 Bilibili API）"""
    loading_text = f"🔄 正在向 B 站请求 {up_name} 的第 {page} 页视频..."
    if is_edit:
        await msg_obj.edit_text(loading_text)
    else:
        msg_obj = await msg_obj.answer(loading_text)
        
    _raw_count, videos = await get_up_videos(uid, pn=page, ps=10)
    
    builder = InlineKeyboardBuilder()
    if not videos:
        txt = f"📺 **{up_name} 的投稿视频** (第 {page} 页)\n\n❌ 抱歉，获取失败或此页已无更多内容返回。"
    else:
        txt = f"📺 **{up_name} 的投稿视频** (第 {page} 页)\n*点击下方对应序号的按钮，机器人将立即开始下载并发送给你！*\n\n"
        for idx, v in enumerate(videos, 1):
            txt += f"`{idx}.` {v['title']}\n"
            # Limit callback data length, pass download command
            builder.row(InlineKeyboardButton(
                text=f"📥 下载第 {idx} 个: {v['title'][:12]}...",
                callback_data=f"directdl_{v['bvid']}"
            ))
            
    # Nav buttons
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(
            text="⬅️ 上一页",
            callback_data=f"sub_v_p_{uid}_{page - 1}"
        ))
    if videos and len(videos) == 10:
        nav_row.append(InlineKeyboardButton(
            text="下一页 ➡️",
            callback_data=f"sub_v_p_{uid}_{page + 1}"
        ))
        
    if nav_row:
        builder.row(*nav_row)
        
    builder.row(InlineKeyboardButton(
        text="🔙 返回订阅详情",
        callback_data=f"sub_detail_{uid}"
    ))
    await msg_obj.edit_text(txt, reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("sub_v_p_"))
async def cb_sub_v_p(callback: types.CallbackQuery):
    """处理视频列表分页"""
    # Format: sub_v_p_{uid}_{page}  → rsplit gives ['sub_v_p', uid, page]
    prefix, uid, page_str = callback.data.rsplit("_", 2)
    page = int(page_str)
    
    subs = await get_user_subscriptions(callback.from_user.id)
    sub = next((s for s in subs if s.uid == uid), None)
    up_name = sub.up_name if sub and sub.up_name else f"UID {uid}"
    
    await show_up_videos_gui(callback.message, uid, up_name, page, is_edit=True)


# --- Direct Download ---
@router.callback_query(F.data.startswith("directdl_"))
async def cb_directdl(callback: types.CallbackQuery, state: FSMContext):
    """处理直接下载按钮"""
    bvid = callback.data.replace("directdl_", "")
    await callback.answer("任务已安排！", show_alert=False)
    url = f"https://www.bilibili.com/video/{bvid}"
    # 导入 download 模块的函数
    from bot.handlers.download import trigger_download_selection
    await trigger_download_selection(callback.message, state, url)
