"""
bot/handlers/download.py - 下载相关处理

包含：
- 视频链接识别
- 画质选择
- 分P选择
- 下载执行
"""

import asyncio
import hashlib
import logging
import re
import shutil
import time
from pathlib import Path

from aiogram import Router, types, F
from aiogram.types import InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from bot.config import (
    BBDOWN_PATH, DATA_DIR, is_admin, VIDEO_EXT, AUDIO_EXT,
    QUALITY_OPTIONS, QUALITY_PRIORITY, DEFAULT_QUALITY
)
from bot.subprocess_executor import (
    SubprocessExecutor, run_bbdown_simple,
    DEFAULT_DOWNLOAD_TIMEOUT, DEFAULT_INFO_TIMEOUT, create_progress_bar
)
from bot.utils import sort_downloaded_files, parse_pages
from bot.database import get_user_settings

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(lambda msg: msg.from_user is not None and is_admin(msg.from_user.id))
router.callback_query.filter(lambda c: c.from_user is not None and is_admin(c.from_user.id))

# BBDown 输出解析常量
BBDOWN_TITLE_PREFIX = "视频标题:"
BBDOWN_PAGES_PATTERN = re.compile(r'(\d+)\s*个分P')
BBDOWN_PART_PATTERN = re.compile(r"-\s*P(\d+):\s*\[([^\]]+)\]\s*\[(.*)\]\s*\[([^\]]+)\]")

# 匹配消息中的 Bilibili / b23.tv URL（兼容手机/电脑分享格式）
# 手机分享: 【标题】 https://b23.tv/Da7S8SH
# 电脑分享: 【标题】 https://www.bilibili.com/video/BV.../?share_source=...
# 带时间戳: 【标题】 【精准空降到 00:07】 https://...&t=7
URL_PATTERN = re.compile(r"https?://(?:www\.)?(?:bilibili\.com|b23\.tv)/[^\s】]+")


# --- FSM States ---
class DownloadSession(StatesGroup):
    """用于存储用户当前正在配置的下载任务上下文"""
    waiting_for_quality = State()  # 等待选择画质
    waiting_for_pages = State()    # 等待输入分P范围


@router.message(F.text)
async def handle_bilibili_link(message: types.Message, state: FSMContext):
    """处理 Bilibili 链接（兼容手机/电脑分享格式）"""
    if not message.text:
        return
    
    # 从消息中搜索 Bilibili URL
    match = URL_PATTERN.search(message.text)
    if not match:
        return  # 不是 Bilibili 链接，交给其他 handler
    
    await state.clear()
    # 清理 URL 末尾可能残留的标点（中文句号、顿号等）
    url = match.group(0).rstrip("。，、；：！？…—")
    await trigger_download_selection(message, state, url)


async def trigger_download_selection(message: types.Message, state: FSMContext, url: str):
    """解析视频并让用户选择格式/分P"""
    await state.clear()
    
    status_msg = await message.answer(f"🔍 解析视频: `{url}`...", parse_mode="Markdown")
    
    try:
        info = await get_video_info(url)
    except FileNotFoundError as e:
        logger.error(f"BBDown 可执行文件未找到: {e}")
        await status_msg.edit_text(
            f"❌ **解析失败：BBDown 未安装或路径错误**\n\n"
            f"请检查服务器上 BBDown 是否正确安装，环境变量是否配置。\n"
            f"当前配置路径: `{BBDOWN_PATH}`",
            parse_mode="Markdown"
        )
        return
    except asyncio.TimeoutError:
        await status_msg.edit_text(
            f"❌ **解析超时**\n\n"
            f"BBDown 执行超过 {DEFAULT_INFO_TIMEOUT} 秒未响应，可能是网络问题或服务器负载过高。",
            parse_mode="Markdown"
        )
        return
    except Exception as e:
        logger.error(f"解析视频时发生未知错误: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ 解析时发生未知错误: `{e}`", parse_mode="Markdown")
        return
    
    if not info:
        # 获取最近一次 BBDown 执行的错误输出
        try:
            result = await run_bbdown_simple([url, "--only-show-info"], DATA_DIR, timeout=30)
            error_detail = result.output[:300] if result.output else "无详细错误信息"
        except FileNotFoundError:
            error_detail = f"BBDown 可执行文件未找到: {BBDOWN_PATH}"
        except Exception:
            error_detail = "(无法获取详细错误信息)"
        
        # 常见错误模式识别
        if "未登录" in error_detail or "login" in error_detail.lower():
            error_hint = "🔐 **可能原因：未登录 B站**\n请先发送 /login 进行扫码登录。"
        elif "地区" in error_detail or "region" in error_detail.lower():
            error_hint = "🌍 **可能原因：地区限制**\n该视频可能有地区访问限制。"
        elif "版权" in error_detail or "copyright" in error_detail.lower():
            error_hint = "🔒 **可能原因：版权限制**\n该视频可能因版权原因不可下载。"
        elif "不存在" in error_detail or "deleted" in error_detail.lower():
            error_hint = "🗑️ **可能原因：视频已删除**\n该视频可能已被 UP 主删除。"
        elif "未找到" in error_detail or "not found" in error_detail.lower():
            error_hint = f"❌ **BBDown 未安装**\n路径: `{BBDOWN_PATH}`"
        else:
            error_hint = f"```\n{error_detail}\n```"
        
        await status_msg.edit_text(
            f"❌ **解析失败**\n\n"
            f"{error_hint}\n\n"
            f"💡 如问题持续，请检查 `/settings` → 登录状态，确认凭证有效。",
            parse_mode="Markdown"
        )
        return
        
    # 使用 FSMContext 存储会话状态
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
            if is_first_chunk:
                await status_msg.edit_text(chunk_text, parse_mode="Markdown")
            else:
                await message.answer(chunk_text, parse_mode="Markdown")
        action_msg_text = "📺 这是一个多 P 播放列表，请选择下载格式："
    else:
        await status_msg.edit_text(f"📺 **{info['title']}**\n(Total Pages: 1)")
        action_msg_text = "请选择你要提取的格式："
    
    # 获取用户默认画质设置
    user_settings = await get_user_settings(message.chat.id)
    default_quality = user_settings.get("default_quality", DEFAULT_QUALITY) if user_settings else DEFAULT_QUALITY
    default_quality_name = QUALITY_OPTIONS.get(default_quality, "最高画质")
    
    builder = InlineKeyboardBuilder()
    # 默认设置按钮
    builder.row(InlineKeyboardButton(text=f"⚙️ 默认设置 ({default_quality_name})", callback_data="dlq_default"))
    
    # 画质选择按钮 - 从配置动态生成
    for action, display_name in QUALITY_OPTIONS.items():
        if action != "best":  # best 在默认设置中显示
            builder.row(InlineKeyboardButton(text=display_name, callback_data=f"dlq_{action}"))
    
    await message.answer(action_msg_text, reply_markup=builder.as_markup(), parse_mode="Markdown")


async def get_video_info(url: str):
    """
    解析视频信息，返回标题、分P数等。
    
    Returns:
        成功时返回 dict，失败时返回 None
    """
    result = await run_bbdown_simple([url, "--only-show-info", "--show-all"], DATA_DIR, timeout=DEFAULT_INFO_TIMEOUT)
    
    # 先尝试解析输出，即使 return_code != 0 也可能包含有效信息
    title = None
    total_pages = 1
    parts = []

    for line in result.output.split('\n'):
        if BBDOWN_TITLE_PREFIX in line:
            title = line.split(BBDOWN_TITLE_PREFIX, 1)[1].strip()
        m = BBDOWN_PAGES_PATTERN.search(line)
        if m:
            total_pages = int(m.group(1))
        part_match = BBDOWN_PART_PATTERN.match(line)
        if part_match:
            parts.append({"index": int(part_match.group(1)), "title": part_match.group(3).strip()})

    # 如果成功解析到标题，认为解析成功
    if title:
        return {"title": title, "total_pages": total_pages, "parts": parts}
    
    # 只有在没有解析到任何有用信息时才报错
    if result.return_code != 0:
        error_snippet = result.output[:500] if result.output else "(无输出)"
        logger.error(f"❌ BBDown 解析失败 [{url}]: return_code={result.return_code}\n完整输出:\n{error_snippet}")
    
    return None


@router.callback_query(F.data.startswith("dlq_"))
async def handle_quality_selection(callback: types.CallbackQuery, state: FSMContext):
    """处理画质选择，单P视频立即开始下载"""
    data = await state.get_data()
    if not data or "url" not in data:
        return await callback.answer("会话已过期，请重新发送视频链接。", show_alert=True)
    
    action = callback.data.replace("dlq_", "")
    
    # 处理"默认设置"：从数据库读取用户设置的默认画质
    if action == "default":
        user_settings = await get_user_settings(callback.message.chat.id)
        action = user_settings.get("default_quality", DEFAULT_QUALITY) if user_settings else DEFAULT_QUALITY
    
    # 将画质选项存入 FSM，并立即更新 data 字典
    await state.update_data(action=action)
    data["action"] = action  # ← 关键：同步更新本地 data 字典
    
    # 调试：确认 action 已正确设置
    logger.info(f"🎬 画质选择回调: action='{action}', data.action='{data.get('action')}'")
    
    total_pages = data.get("total_pages", 1)
    
    # 单 P 视频：直接开始下载
    if total_pages == 1:
        await callback.answer("🚀 开始下载...")
        await start_multi_download(callback.message, data, [1])
        return
    
    # 多 P 视频：显示分P选择
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📥 下载所有 P (批量)", callback_data="dlp_all"))
    builder.row(InlineKeyboardButton(text="🔽 仅下载 P1", callback_data="dlp_1"))
    builder.row(InlineKeyboardButton(text="✏️ 自定义 P 数范围", callback_data="dlp_custom"))
    await callback.message.edit_text(
        f"**这是一个合集视频** (总共 {total_pages} P)。\n您希望下载哪些章节？",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("dlp_"))
async def handle_page_selection(callback: types.CallbackQuery, state: FSMContext):
    """处理分P选择"""
    data = await state.get_data()
    if not data or "url" not in data:
        return await callback.answer("会话已过期，请重新发送视频链接。", show_alert=True)
    
    page_action = callback.data.replace("dlp_", "")
    total_pages = data.get("total_pages", 1)
    
    if page_action == "custom":
        await state.set_state(DownloadSession.waiting_for_pages)
        await callback.message.edit_text(
            "✏️ **请求自定义输入页数：**\n\n"
            "请直接回复本条，输入你想下载的页面。\n"
            "举个例子：\n"
            "`1-3`（代表下载 1,2,3 分P）\n"
            "`1,4,7`（代表离散下载）",
            parse_mode="Markdown"
        )
        return
    elif page_action == "all":
        pages = list(range(1, total_pages + 1))
    elif page_action == "1":
        pages = [1]
    else:
        pages = [1]
    
    # 从 FSM 获取完整 session 数据并开始下载
    await start_multi_download(callback.message, data, pages)


@router.message(DownloadSession.waiting_for_pages)
async def process_custom_pages(message: types.Message, state: FSMContext):
    """处理自定义分P输入"""
    data = await state.get_data()
    if not data or "url" not in data:
        await state.clear()
        return await message.answer("会话已过期，请重新发送视频链接。")
        
    text = message.text.strip()
    total_pages = data.get("total_pages", 1)
    
    try:
        pages = parse_pages(text, total_pages)
    except ValueError as e:
        return await message.answer(
            f"❌ 格式错误：{e}\n\n"
            f"请重试或发送新的网址取消操作。\n"
            f"正确格式例如: `1-5,7` 或 `1,3,5`",
            parse_mode="Markdown"
        )
        
    await state.clear()
    status_msg = await message.answer("✅ 自定义页数锁定，开始提取队列...")
    await start_multi_download(status_msg, data, pages)


async def start_multi_download(status_msg: types.Message, session: dict, pages: list[int]):
    """开始多P下载任务"""
    if not session or "url" not in session:
        return await status_msg.edit_text("会话数据无效。")
        
    url = session["url"]
    action = session.get("action", "best")
    title = session.get("title", "Unknown")
    
    # 调试：确认 action 值
    logger.info(f"🎯 用户选择 action: '{action}' (类型: {type(action).__name__})")
    logger.info(f"📋 QUALITY_PRIORITY 包含的 keys: {list(QUALITY_PRIORITY.keys())}")
    
    cmd_args = [url]
    quality_text = "最高画质"
    
    # 画质选择 - 使用 BBDown 的画质优先级参数
    # 重要：BBDown 的 -q 是优先级列表，会选择列表中第一个可用的画质名称
    # 如果视频没有提供列表中的任何画质，BBDown 会使用默认最高画质
    if action == "audio":
        cmd_args.append("--audio-only")
        quality_text = "仅音频"
    elif action == "danmaku":
        cmd_args.append("--danmaku")
        quality_text = "仅弹幕"
    elif action == "sub":
        cmd_args.append("--sub-only")
        quality_text = "仅字幕"
    elif action in QUALITY_PRIORITY:
        priority_list = QUALITY_PRIORITY[action]
        if priority_list:  # 空列表表示不限制画质
            quality_arg = ",".join(priority_list)
            cmd_args.extend(["-q", quality_arg])
            quality_text = {"1080": "1080P", "720": "720P", "480": "480P", "360": "360P"}.get(action, action)
            logger.info(f"🎨 画质限制: {action} -> -q {quality_arg[:80]}...")
    else:
        quality_text = "最高画质"
    
    # 调试：打印完整的 BBDown 命令（包含所有参数）
    cmd_str = ' '.join(str(x) for x in cmd_args)
    logger.info(f"🔧 BBDown 完整命令: {BBDOWN_PATH} {cmd_str}")
    logger.info(f"🔧 cmd_args 列表: {cmd_args}")
    
    # 使用 URL hash 作为下载目录标识
    dl_id = hashlib.md5(url.encode()).hexdigest()[:8]
    dl_base = Path(DATA_DIR) / "downloads" / dl_id
    dl_base.mkdir(parents=True, exist_ok=True)
    
    await status_msg.edit_text(
        f"🚀 **开始下载**\n📺 `{title}`\n🎨 画质: {quality_text}\n📦 分P: {len(pages)} 个",
        parse_mode="Markdown"
    )
    
    try:
        for i, p in enumerate(pages):
            # 每个分P使用独立目录
            dl_dir = dl_base / f"p{p}"
            dl_dir.mkdir(parents=True, exist_ok=True)
            
            current_cmd_args = cmd_args.copy()
            current_cmd_args.extend(["-p", str(p), "--work-dir", str(dl_dir.absolute())])
            
            # 进度状态 - last_update_time=0 确保第一次立即显示
            download_start_time = time.time()
            last_update_time = -100.0  # 改为负数确保第一次进度立即显示
            last_percentage = -1.0  # 改为-1确保0%也能触发
            current_text = ""
            downloaded_size = 0  # MB
            
            async def update_progress(status: str, percentage: float = None, extra: str = ""):
                """更新进度显示，1秒节流"""
                nonlocal last_update_time, current_text
                current_time = time.time()
                if current_time - last_update_time < 1.0 and not status.startswith(("✅", "❌", "☁️")):
                    return
                
                elapsed = current_time - download_start_time
                elapsed_str = f"{int(elapsed // 60)}:{int(elapsed % 60):02d}"
                
                if percentage is not None:
                    bar = create_progress_bar(percentage)
                    text = f"📥 **下载 P{p}** ({i+1}/{len(pages)})\n`{bar}` {percentage:.1f}%\n⏱️ {elapsed_str}\n{extra}"
                else:
                    text = f"📥 **{status}**\n⏱️ {elapsed_str}\n{extra}"
                
                if text != current_text:
                    try:
                        await status_msg.edit_text(text, parse_mode="Markdown")
                        current_text = text
                        last_update_time = current_time
                    except Exception:
                        pass

            try:
                await status_msg.edit_text(
                    f"📥 **开始下载 P{p}** ({i+1}/{len(pages)})\n📺 `{title}`\n🎨 画质: {quality_text}",
                    parse_mode="Markdown"
                )
                
                # 构建完整的 BBDown 命令
                bbdown_cmd = [BBDOWN_PATH] + current_cmd_args
                logger.debug(f"🔧 执行命令: {' '.join(bbdown_cmd)}")
                
                # 改进进度更新逻辑：独立文件扫描任务
                min_update_interval = 1.0  # 1秒刷新一次
                
                # 查找正在下载的文件（视频和音频）
                def scan_downloading_files():
                    """只统计原始分片文件（.m4s/.ts），排除合并后的最终文件"""
                    # 第一步：只统计 BBDown 下载的原始分片
                    fragment_size = 0.0
                    found_fragments = []
                    
                    for search_dir in [dl_dir, dl_base]:
                        if not search_dir.exists():
                            continue
                        for f in search_dir.rglob("*"):
                            if not f.is_file():
                                continue
                            ext = f.suffix.lower()
                            # 只统计原始分片文件，排除合并输出
                            if ext in ['.m4s', '.ts']:
                                try:
                                    size_mb = f.stat().st_size / (1024 * 1024)
                                    fragment_size += size_mb
                                    found_fragments.append(f"{f.name}: {size_mb:.1f}MB")
                                except:
                                    pass
                    
                    # 如果找到分片文件，返回分片总和
                    if found_fragments:
                        return fragment_size, found_fragments
                    
                    # 第二步：分片已删除（合并阶段或音频下载期间），回退到扫描最大文件
                    # 但要排除已完成的大文件（视频合并完成后的mp4）
                    all_files = []
                    exclude_names = {'.mp4', '.flv', '.mkv', '.avi'}
                    for search_dir in [dl_dir, dl_base]:
                        if not search_dir.exists():
                            continue
                        for f in search_dir.rglob("*"):
                            if f.is_file():
                                ext = f.suffix.lower()
                                # 排除已完成的大文件（这些是合并产物）
                                if ext in exclude_names and f.stat().st_size > 1024 * 1024 * 10:  # > 10MB
                                    continue
                                if ext not in ['.jpg', '.png', '.txt', '.log', '.json']:
                                    try:
                                        size_mb = f.stat().st_size / (1024 * 1024)
                                        all_files.append((f.name, size_mb))
                                    except:
                                        pass
                    
                    if all_files:
                        all_files.sort(key=lambda x: x[1], reverse=True)
                        largest = all_files[0][1]
                        return largest, [f"{n}: {s:.1f}MB" for n, s in all_files]
                    
                    return 0.0, []
                
                # 从 BBDown 输出中解析实际选择的视频和音频大小
                # 格式: [视频] [360P 流畅] [640x360] [AVC] [30.000] [346 kbps] [~8.95 MB]
                # 格式: [音频] [M4A] [181 kbps] [~4.68 MB]
                video_size_estimate = 0.0
                audio_size_estimate = 0.0
                
                def parse_size_from_line(line):
                    """从 BBDown 输出行解析大小（MB）"""
                    match = re.search(r'\[~([\d.]+)\s*MB\]', line)
                    if match:
                        return float(match.group(1))
                    return 0.0
                
                # 初始扫描
                initial_size, _ = scan_downloading_files()
                last_file_size = initial_size
                download_active = True  # 标记下载是否进行中
                expected_total_size = 0.0
                
                # 阶段追踪：video -> audio -> merging
                current_phase = "video"  # "video" | "audio" | "merging"
                video_phase_done = False  # 视频分片下载是否完成
                
                # ========== 核心修复：独立的文件扫描任务 ==========
                # 问题：async for 只在 BBDown 输出时推进，如果 BBDown 下载时不输出，扫描就不会执行
                # 解决：使用 asyncio.create_task 创建独立任务，每秒扫描一次
                
                async def file_scan_loop():
                    """独立的文件扫描任务，不依赖 BBDown 输出"""
                    nonlocal last_file_size, download_active, expected_total_size, video_size_estimate, audio_size_estimate, current_phase, video_phase_done
                    
                    while download_active:
                        await asyncio.sleep(1.0)
                        if not download_active:
                            break
                        
                        current_file_size, found_files = scan_downloading_files()
                        
                        # 更新预估总大小
                        expected_total_size = video_size_estimate + audio_size_estimate
                        
                        logger.debug(f"🔍 文件扫描: 大小={current_file_size:.1f}MB, 文件数={len(found_files)}, 预估={expected_total_size:.1f}MB, 阶段={current_phase}")
                        
                        # 处理空文件情况（音频刚开始时）
                        if expected_total_size > 0:
                            if current_file_size == 0 and current_phase == "audio":
                                # 音频刚开始下载，还未写入文件
                                extra = f"🎵 音频下载中... ({audio_size_estimate:.1f} MB)"
                                await update_progress("下载中", None, extra)
                                last_file_size = 0
                                continue
                            elif current_file_size == 0 and current_phase == "video" and video_size_estimate > 0:
                                # 视频刚开始下载，还未写入文件
                                extra = f"📹 视频下载中... ({video_size_estimate:.1f} MB)"
                                await update_progress("下载中", None, extra)
                                last_file_size = 0
                                continue
                        
                        if found_files and current_file_size > 0 and expected_total_size > 0:
                            # 根据阶段计算累计进度
                            if current_phase == "video":
                                # 视频阶段：当前分片大小
                                cumulative = current_file_size
                            elif current_phase == "audio":
                                # 音频阶段：视频已完成 + 当前音频分片大小
                                cumulative = video_size_estimate + current_file_size
                            else:
                                # 合并阶段：显示接近完成
                                cumulative = expected_total_size * 0.99
                            
                            pct = min(99.0, cumulative / expected_total_size * 100)
                            
                            # 构建显示文本
                            if current_phase == "merging":
                                extra = f"🔄 合并音视频中... ({expected_total_size:.1f} MB)"
                            else:
                                extra = f"📦 {cumulative:.1f}/{expected_total_size:.1f} MB ({pct:.0f}%)"
                            
                            # 计算下载速度
                            if current_file_size > last_file_size:
                                growth_rate = current_file_size - last_file_size  # 每秒 MB
                                if growth_rate > 0:
                                    extra += f" | ⚡ {growth_rate:.1f} MB/s"
                            
                            await update_progress("下载中", pct, extra)
                            
                            if current_file_size > last_file_size + 0.5:
                                logger.info(f"📥 下载进度: {cumulative:.1f}/{expected_total_size:.1f} MB ({pct:.0f}%) | {len(found_files)} 个文件")
                        
                        last_file_size = current_file_size
                
                # 创建文件扫描任务
                executor = SubprocessExecutor(timeout=DEFAULT_DOWNLOAD_TIMEOUT)
                scan_task = asyncio.create_task(file_scan_loop())
                
                try:
                    # 构建完整的 BBDown 命令
                    bbdown_cmd = [BBDOWN_PATH] + current_cmd_args
                    logger.debug(f"🔧 执行命令: {' '.join(bbdown_cmd)}")
                    
                    async for progress in executor.run_with_progress(bbdown_cmd, DATA_DIR):
                        line = progress.line or ""
                        
                        # 解析实际选择的视频和音频大小
                        if video_size_estimate == 0 and "[视频]" in line:
                            video_size_estimate = parse_size_from_line(line)
                            if video_size_estimate > 0:
                                logger.info(f"📊 视频大小: {video_size_estimate:.1f} MB")
                        if audio_size_estimate == 0 and "[音频]" in line:
                            audio_size_estimate = parse_size_from_line(line)
                            if audio_size_estimate > 0:
                                logger.info(f"📊 音频大小: {audio_size_estimate:.1f} MB")
                        
                        # 监听 BBDown 输出判断当前阶段
                        if "合并" in line or "开始合并" in line:
                            current_phase = "merging"
                            logger.info(f"🔄 进入合并阶段")
                        elif "音频" in line and ("下载" in line or "开始" in line):
                            if current_phase == "video" and not video_phase_done:
                                video_phase_done = True
                            current_phase = "audio"
                            logger.info(f"🎵 进入音频阶段")
                        
                        # 处理 BBDown 的进度输出（如果有百分比）
                        pct = progress.percentage
                        if pct > 0:
                            extra = f"📦 {progress.size}" if progress.size else ""
                            if progress.speed:
                                extra += f" | ⚡ {progress.speed}"
                            await update_progress("下载中", pct, extra)
                            last_percentage = pct
                    
                    result = await executor.wait()
                finally:
                    # 停止文件扫描任务
                    download_active = False
                    scan_task.cancel()
                    try:
                        await scan_task
                    except asyncio.CancelledError:
                        pass
                
                # 从 BBDown 输出中提取实际选择的画质信息
                actual_quality = None
                for line in result.output.split('\n') if result.output else []:
                    # BBDown 输出格式示例: "已选择清晰度: 1080P 高码率" 或 "Selected quality: 1080P"
                    if "清晰度" in line or "quality" in line.lower() or "dfn" in line.lower():
                        logger.info(f"📺 画质选择输出: {line.strip()}")
                        # 尝试提取画质信息
                        for q in ["1080P", "720P", "480P", "360P", "8K", "4K", "HDR", "杜比"]:
                            if q in line:
                                actual_quality = q
                                break
                
                if actual_quality:
                    logger.info(f"📺 实际下载画质: {actual_quality}")
                
            except asyncio.CancelledError:
                await executor.kill()
                raise
            except (OSError, RuntimeError) as e:
                logger.error(f"P{p} subprocess error: {e}", exc_info=True)
                await executor.kill()
                await status_msg.answer(f"❌ P{p} 下载出错: {e}")
                continue
            except Exception as e:
                logger.error(f"P{p} unexpected error: {e}", exc_info=True)
                await executor.kill()
                await status_msg.answer(f"❌ P{p} 下载出错: {e}")
                continue
            
            if result.timed_out:
                await status_msg.answer(
                    f"❌ **P{p} 下载超时** (超时 {DEFAULT_DOWNLOAD_TIMEOUT//60} 分钟)",
                    parse_mode="Markdown"
                )
                continue
            
            # 记录非零返回码（但可能仍有成功下载的文件）
            if result.return_code != 0:
                logger.warning(f"⚠️ P{p} BBDown 返回非零退出码 {result.return_code}，检查文件...")

            await update_progress("☁️ 上传到 Telegram...", None, f"📦 {downloaded_size:.1f} MB")
            await asyncio.sleep(0.5)
            
            downloaded_files = [
                f for f in dl_dir.rglob("*")
                if f.is_file() and f.suffix.lower() not in ['.jpg', '.png']
            ]
            
            if not downloaded_files:
                # 只有在没有文件且返回码非零时才报错
                if result.return_code != 0:
                    error_lines = result.output.strip().split('\n')[-20:] if result.output else []
                    error_detail = '\n'.join(error_lines)[-500:] if error_lines else "(无输出)"
                    logger.error(f"❌ P{p} 下载失败: return_code={result.return_code}\n{error_detail}")
                    await status_msg.answer(
                        f"❌ **P{p} 下载失败** (错误代码 {result.return_code})\n\n"
                        f"```\n{error_detail}\n```",
                        parse_mode="Markdown"
                    )
                else:
                    await status_msg.answer(f"❌ 查无打包文件。可能 P{p} 已受版权封存导致无文件导出。")
                continue

            # 按类型排序后发送：视频 → 音频 → 其他
            downloaded_files = sort_downloaded_files(downloaded_files)
            
            # 计算总文件大小
            total_size = sum(f.stat().st_size for f in downloaded_files) / (1024 * 1024)  # MB
            
            # 显示上传进度
            file_count = len(downloaded_files)
            await status_msg.edit_text(
                f"☁️ **准备上传** P{p}\n📦 {total_size:.1f} MB ({file_count} 个文件)\n⏳ 正在连接 Telegram...",
                parse_mode="Markdown"
            )

            try:
                sent_count = 0
                upload_start_time = time.time()
                
                for f in downloaded_files:
                    ext = f.suffix.lower()
                    file_size_mb = f.stat().st_size / (1024 * 1024)
                    cap = f"{title} (P{p})"
                    
                    # 大文件提示 - 使用长时间超时
                    if file_size_mb > 50:
                        await status_msg.edit_text(
                            f"☁️ **上传大文件** {sent_count + 1}/{file_count}\n"
                            f"📦 {file_size_mb:.1f} MB\n"
                            f"⏳ Telegram 服务器响应较慢，请耐心等待...\n"
                            f"💡 提示：上传不会显示进度条，但正在进行中",
                            parse_mode="Markdown"
                        )
                    elif file_count > 1:
                        await status_msg.edit_text(
                            f"☁️ **上传中** {sent_count + 1}/{file_count}\n"
                            f"📦 {file_size_mb:.1f} MB",
                            parse_mode="Markdown"
                        )
                    
                    # 使用 BufferInputFile 替代 FSInputFile，支持更好的进度控制
                    # 对于大文件，增加超时容忍度
                    try:
                        if ext in VIDEO_EXT:
                            await status_msg.answer_video(
                                FSInputFile(str(f)),
                                caption=cap,
                                request_timeout=300  # 5 分钟超时
                            )
                        elif ext in AUDIO_EXT:
                            await status_msg.answer_audio(
                                FSInputFile(str(f)),
                                caption=cap,
                                request_timeout=300
                            )
                        else:
                            await status_msg.answer_document(
                                FSInputFile(str(f)),
                                caption=cap,
                                request_timeout=300
                            )
                    except Exception as send_err:
                        # 如果超时但文件可能已发送，不重复发送
                        err_str = str(send_err).lower()
                        if "timeout" in err_str:
                            await status_msg.edit_text(
                                f"⚠️ **上传响应超时**\n"
                                f"文件可能已成功发送，请检查聊天记录。\n"
                                f"文件: {f.name}",
                                parse_mode="Markdown"
                            )
                            # 继续处理下一个文件，不要中断整个流程
                            sent_count += 1
                            continue
                        else:
                            raise send_err
                    
                    sent_count += 1
                    
                    # 更新上传进度（多文件时）
                    if file_count > 1 and sent_count < file_count:
                        upload_elapsed = time.time() - upload_start_time
                        await status_msg.edit_text(
                            f"☁️ **已上传** {sent_count}/{file_count}\n"
                            f"⏱️ {int(upload_elapsed // 60)}:{int(upload_elapsed % 60):02d}",
                            parse_mode="Markdown"
                        )
                
                elapsed_total = time.time() - download_start_time
                await status_msg.edit_text(
                    f"✅ **P{p} 完成！**\n📦 {total_size:.1f} MB | ⏱️ {int(elapsed_total // 60)}:{int(elapsed_total % 60):02d}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send file: {e}", exc_info=True)
                # 检查是否是超时错误
                err_str = str(e).lower()
                if "timeout" in err_str:
                    await status_msg.answer(
                        f"⚠️ **上传超时**\n"
                        f"文件可能已发送成功，请检查聊天记录。\n"
                        f"如未收到，可重新下载。\n\n"
                        f"错误: {e}"
                    )
                else:
                    await status_msg.answer(f"❌ 推送失败：{e}")

    finally:
        # 全部P处理完毕后统一清理
        shutil.rmtree(dl_base, ignore_errors=True)
