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

from bot.config import BBDOWN_PATH, DATA_DIR, is_admin, VIDEO_EXT, AUDIO_EXT
from bot.subprocess_executor import (
    SubprocessExecutor, run_bbdown_simple,
    DEFAULT_DOWNLOAD_TIMEOUT, DEFAULT_INFO_TIMEOUT, create_progress_bar
)
from bot.utils import sort_downloaded_files, parse_pages

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(lambda msg: msg.from_user is not None and is_admin(msg.from_user.id))
router.callback_query.filter(lambda c: c.from_user is not None and is_admin(c.from_user.id))

# BBDown 输出解析常量
BBDOWN_TITLE_PREFIX = "视频标题:"
BBDOWN_PAGES_PATTERN = re.compile(r'(\d+)\s*个分P')
BBDOWN_PART_PATTERN = re.compile(r"-\s*P(\d+):\s*\[([^\]]+)\]\s*\[(.*)\]\s*\[([^\]]+)\]")

URL_PATTERN = re.compile(r"(https?://(www\.)?(bilibili\.com|b23\.tv)/[^\s]+)")


# --- FSM States ---
class DownloadSession(StatesGroup):
    """用于存储用户当前正在配置的下载任务上下文"""
    waiting_for_quality = State()  # 等待选择画质
    waiting_for_pages = State()    # 等待输入分P范围


@router.message(F.text.regexp(URL_PATTERN))
async def handle_bilibili_link(message: types.Message, state: FSMContext):
    """处理 Bilibili 链接"""
    await state.clear()
    match = URL_PATTERN.search(message.text)
    if not match:
        return
    await trigger_download_selection(message, state, match.group(1))


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
        
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎬 最高画质直出 (默认推荐)", callback_data="dlq_best"))
    builder.row(InlineKeyboardButton(text="🎵 仅提取音频 (MP3/M4A)", callback_data="dlq_audio"))
    builder.row(InlineKeyboardButton(text="📺 单独提取弹幕文件", callback_data="dlq_danmaku"))
    
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
    """处理画质选择"""
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
        await callback.message.edit_text(
            f"**这是一个合集视频** (总共 {total_pages} P)。\n您希望下载哪些章节？",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
    else:
        builder.row(InlineKeyboardButton(text="🚀 开始下载并上传", callback_data="dlp_1"))
        await callback.message.edit_text(
            f"配置完毕，**准备就绪**。\n点击启动后将在后台处理，请耐心等待文件回传。",
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
    
    cmd_args = [url]
    if action == "audio":
        cmd_args.append("--audio-only")
    elif action == "danmaku":
        cmd_args.append("--danmaku")
    elif action == "sub":
        cmd_args.append("--sub-only")
    
    # 使用 URL hash 作为下载目录标识
    dl_id = hashlib.md5(url.encode()).hexdigest()[:8]
    dl_base = Path(DATA_DIR) / "downloads" / dl_id
    dl_base.mkdir(parents=True, exist_ok=True)
    
    await status_msg.edit_text(f"🚀 已排队 {len(pages)} 个任务。\n当前: P{pages[0]}")
    
    try:
        for i, p in enumerate(pages):
            # 每个分P使用独立目录
            dl_dir = dl_base / f"p{p}"
            dl_dir.mkdir(parents=True, exist_ok=True)
            
            current_cmd_args = cmd_args.copy()
            current_cmd_args.extend(["-p", str(p), "--work-dir", str(dl_dir.absolute())])
            
            try:
                await status_msg.edit_text(
                    f"📥 **拉取数据库 P{p}** ({i+1}/{len(pages)})...\n标题: `{title}`",
                    parse_mode="Markdown"
                )
            except:
                pass
            
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
                        except Exception:
                            pass

            try:
                async for progress in executor.run_with_progress([BBDOWN_PATH] + current_cmd_args, DATA_DIR):
                    if abs(progress.percentage - last_percentage) >= 5.0 or (time.time() - last_update_time) >= 3.0:
                        bar = create_progress_bar(progress.percentage)
                        await flush_ui(f"`{bar}`", force=True)
                        last_percentage = progress.percentage
                    elif progress.percentage == 100.0:
                        await flush_ui(f"🔄 **打包编码封装中，请等候...**", force=True)
                
                result = await executor.wait()
                
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
                    f"❌ **P{p} 下载超时，已强制终止 (超时 {DEFAULT_DOWNLOAD_TIMEOUT//60} 分钟)**。",
                    parse_mode="Markdown"
                )
                continue
            
            # 记录非零返回码（但可能仍有成功下载的文件）
            if result.return_code != 0:
                logger.warning(f"⚠️ P{p} BBDown 返回非零退出码 {result.return_code}，但检查是否有成功下载的文件...")

            await flush_ui("☁️ **准备向 Telegram Cloud 上传结果...**", force=True)
            await asyncio.sleep(1.5)
            
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

            try:
                for f in downloaded_files:
                    ext = f.suffix.lower()
                    fobj = FSInputFile(str(f))
                    cap = f"{title} (P{p})"
                    if ext in VIDEO_EXT:
                        await status_msg.answer_video(fobj, caption=cap)
                    elif ext in AUDIO_EXT:
                        await status_msg.answer_audio(fobj, caption=cap)
                    else:
                        await status_msg.answer_document(fobj, caption=cap)
                await flush_ui(f"✅ **P{p} 传输完毕！**", force=True)
            except Exception as e:
                logger.error(f"Failed to send file: {e}")
                await status_msg.answer(f"❌ 推送限制引发失败或阻断： {e}")

    finally:
        # 全部P处理完毕后统一清理
        shutil.rmtree(dl_base, ignore_errors=True)
