import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional, Dict

from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from config import BBDOWN_PATH, DATA_DIR, is_admin
from database import add_subscription, remove_subscription, get_user_subscriptions

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(lambda msg: msg.from_user is not None and is_admin(msg.from_user.id))

URL_PATTERN = re.compile(r"(https?://(www\.)?(bilibili\.com|b23\.tv)/[^\s]+)")
PROGRESS_PATTERN = re.compile(r"(\d+(\.\d+)?)%")

# States for FSM
class DownloadFSM(StatesGroup):
    waiting_for_pages = State()

# Temporary storage for video info per user
user_sessions: Dict[int, dict] = {}

def create_progress_bar(percentage: float, length: int = 15) -> str:
    filled = int(length * percentage / 100)
    empty = length - filled
    return f"[{'█' * filled}{'░' * empty}] {percentage:.1f}%"

@router.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message):
    # /subscribe UID [keyword]
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer("Usage: /subscribe <UID> [keyword]")
        return
        
    uid = args[1]
    keyword = args[2] if len(args) > 2 else None
    
    success = await add_subscription(uid, message.chat.id, keyword)
    if success:
        await message.answer(f"✅ Successfully subscribed to Bilibili UID: {uid}" + (f" with keyword: {keyword}" if keyword else ""))
    else:
        await message.answer("❌ You are already subscribed to this UID in this chat.")

@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: types.Message):
    # /unsubscribe UID
    args = message.text.split()
    if len(args) < 2:
        # Show list if no kwargs
        subs = await get_user_subscriptions(message.chat.id)
        if not subs:
            await message.answer("You have no active subscriptions.")
            return
            
        text = "Your active subscriptions:\n"
        for sub in subs:
            text += f"- UID: {sub.uid}" + (f" (Keyword: {sub.keyword})" if sub.keyword else "") + "\n"
        text += "\nTo remove one: /unsubscribe <UID>"
        await message.answer(text)
        return
        
    uid = args[1]
    success = await remove_subscription(uid, message.chat.id)
    if success:
        await message.answer(f"✅ Successfully unsubscribed from UID: {uid}")
    else:
        await message.answer(f"❌ Subscription to UID {uid} not found.")

async def get_video_info(url: str) -> Optional[dict]:
    cmd = [BBDOWN_PATH, url, "--only-show-info", "--show-all"]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=DATA_DIR
        )
        stdout, _ = await process.communicate()
    except Exception as e:
        logger.error(f"Error running BBDown --only-show-info: {e}")
        return None

    if process.returncode != 0:
        return None

    try:
        output = stdout.decode('utf-8')
    except UnicodeDecodeError:
        output = stdout.decode('gbk', errors='ignore')
    
    title = "Unknown Title"
    qualities = []
    total_pages = 1
    parts = []
    
    for line in output.split('\n'):
        if "视频标题:" in line:
            title = line.split("视频标题:", 1)[1].strip()
        
        if "个分P" in line:
            m = re.search(r'(\d+)\s*个分P', line)
            if m:
                total_pages = int(m.group(1))
        
        # Parse qualities
        match = re.search(r"^\s*(\d+)\.\s*(.*?)$", line)
        if match and "画质代码:" not in line:
            qualities.append({
                "id": match.group(1),
                "name": match.group(2).strip()
            })
            
        # Parse playlist parts
        part_match = re.search(r"-\s*P(\d+):\s*\[([^\]]+)\]\s*\[(.*)\]\s*\[([^\]]+)\]", line)
        if part_match:
            p_idx = int(part_match.group(1))
            p_title = part_match.group(3).strip()
            parts.append({"index": p_idx, "title": p_title})
            
    return {"title": title, "qualities": qualities, "total_pages": total_pages, "parts": parts}

@router.message(F.text, F.text.regexp(URL_PATTERN))
async def handle_bilibili_link(message: types.Message, state: FSMContext):
    # Clear any pending states
    await state.clear()
    
    match = URL_PATTERN.search(message.text)
    if not match:
        return
        
    url = match.group(1)
    status_msg = await message.answer("Analyzing video...")
    
    info = await get_video_info(url)
    if not info:
        await status_msg.edit_text("Failed to fetch video information. Make sure the link is valid and you have permissions.")
        return
        
    user_sessions[message.from_user.id] = {
        "url": url,
        "title": info["title"],
        "total_pages": info["total_pages"]
    }
    
    parts = info.get("parts", [])
    if parts and len(parts) > 1:
        # We have a multi-part playlist. Let's send the list in chunks to avoid limits
        chunk_text = f"**{info['title']}**\n(Total Pages: {info['total_pages']})\n\n**Playlist Parts:**\n"
        is_first_chunk = True
        
        for p in parts:
            line = f"`{p['index']:03d}` - {p['title']}\n"
            # Telegram message limit is 4096. Keep strings well under that limit.
            if len(chunk_text) + len(line) > 3800:
                if is_first_chunk:
                    await status_msg.edit_text(chunk_text, parse_mode="Markdown")
                    is_first_chunk = False
                else:
                    await message.answer(chunk_text, parse_mode="Markdown")
                chunk_text = ""
                
            chunk_text += line
            
        # Send the final chunk of parts
        if chunk_text:
            if is_first_chunk:
                await status_msg.edit_text(chunk_text, parse_mode="Markdown")
            else:
                await message.answer(chunk_text, parse_mode="Markdown")
                
        # Prepare the next message for download options
        action_msg_text = "Choose format for download:"
    else:
        # Single page, just edit the status message to hold the download options
        await status_msg.edit_text(f"**{info['title']}**\n(Total Pages: 1)")
        action_msg_text = "Choose format:"
        
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎬 Highest Quality (Video+Audio)", callback_data="dlq_best"))
    builder.row(InlineKeyboardButton(text="🎵 Audio Only", callback_data="dlq_audio"))
    builder.row(InlineKeyboardButton(text="📺 Danmaku (XML/ASS)", callback_data="dlq_danmaku"))
    builder.row(InlineKeyboardButton(text="💬 Subtitles Only", callback_data="dlq_sub"))
    
    await message.answer(
        action_msg_text,
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("dlq_"))
async def handle_quality_selection(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in user_sessions:
        await callback.answer("Session expired. Send link again.", show_alert=True)
        return
        
    action = callback.data.replace("dlq_", "")
    user_sessions[user_id]["action"] = action
    total_pages = user_sessions[user_id]["total_pages"]
    
    builder = InlineKeyboardBuilder()
    if total_pages > 1:
        builder.row(InlineKeyboardButton(text="📥 Download All Pages (1 to {})".format(total_pages), callback_data="dlp_all"))
        builder.row(InlineKeyboardButton(text="🔽 Download P1 Only", callback_data="dlp_1"))
        builder.row(InlineKeyboardButton(text="✏️ Custom Pages", callback_data="dlp_custom"))
        
        await callback.message.edit_text(
            f"**Playlist detected** ({total_pages} pages).\nWhich pages do you want to download?",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
    else:
        # Single page, jump straight to download
        builder.row(InlineKeyboardButton(text="✅ Start Download", callback_data="dlp_1"))
        await callback.message.edit_text(
            f"**Ready to download.**\nPress start to begin.",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )

@router.callback_query(F.data.startswith("dlp_"))
async def handle_page_selection(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in user_sessions:
        await callback.answer("Session expired. Send link again.", show_alert=True)
        return
        
    page_action = callback.data.replace("dlp_", "")
    total_pages = user_sessions[user_id]["total_pages"]
    
    if page_action == "custom":
        await state.set_state(DownloadFSM.waiting_for_pages)
        await callback.message.edit_text(
            "**Custom Pages requested:**\nPlease reply with the page numbers you want to download.\n"
            "Examples:\n"
            "`1-3` (Downloads P1, P2, P3)\n"
            "`1,4,7` (Downloads P1, P4, P7)\n"
            "`1-3,5` (Downloads P1, P2, P3, P5)\n\n"
            "Waiting for your input...",
            parse_mode="Markdown"
        )
        return
    elif page_action == "all":
        pages = list(range(1, total_pages + 1))
    elif page_action == "1":
        pages = [1]
    else:
        pages = [1]
        
    await start_multi_download(callback.message, user_id, pages)

@router.message(DownloadFSM.waiting_for_pages)
async def process_custom_pages(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in user_sessions:
        await state.clear()
        await message.answer("Session expired. Send link again.")
        return
        
    text = message.text.replace(" ", "").replace("，", ",")
    total_pages = user_sessions[user_id]["total_pages"]
    pages = []
    
    try:
        parts = text.split(',')
        for part in parts:
            if not part: continue
            if '-' in part:
                start, end = map(int, part.split('-'))
                # Clamp boundaries
                start = max(1, start)
                end = min(total_pages, end)
                if start <= end:
                    pages.extend(range(start, end + 1))
            else:
                p = int(part)
                if 1 <= p <= total_pages:
                    pages.append(p)
                    
        # Remove duplicates and sort
        pages = sorted(list(set(pages)))
        
        if not pages:
            raise ValueError("No valid pages found")
            
    except Exception:
        await message.answer("❌ Invalid format. Please use numbers, commas, and hyphens (e.g., `1-5,7`). Try again, or send a new link to cancel.", parse_mode="Markdown")
        return
        
    await state.clear()
    status_msg = await message.answer("Starting customized download queue...")
    await start_multi_download(status_msg, user_id, pages)

async def start_multi_download(status_msg: types.Message, user_id: int, pages: list[int]):
    session = user_sessions.pop(user_id, None)
    if not session:
        await status_msg.edit_text("Session expired.")
        return
        
    url = session["url"]
    action = session["action"]
    title = session["title"]
    
    cmd_args = [url]
    if action == "audio":
        cmd_args.append("--audio-only")
    elif action == "danmaku":
        cmd_args.append("--danmaku")
    elif action == "sub":
        cmd_args.append("--sub-only")
        
    dl_dir = Path(DATA_DIR) / "downloads" / str(user_id)
    dl_dir.mkdir(parents=True, exist_ok=True)
    
    await status_msg.edit_text(f"Job queued: {len(pages)} pages to download.\nProcessing P{pages[0]}...")
    
    for i, p in enumerate(pages):
        dl_dir.mkdir(parents=True, exist_ok=True)
        current_cmd_args = cmd_args.copy()
        current_cmd_args.extend(["-p", str(p), "--work-dir", str(dl_dir.absolute())])
        
        try:
            await status_msg.edit_text(f"📥 **Downloading P{p}** ({i+1}/{len(pages)})...\nTitle: {title}", parse_mode="Markdown")
        except:
            pass
            
        cmd = [BBDOWN_PATH] + current_cmd_args
        logger.info(f"Executing: {' '.join(cmd)}")
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=DATA_DIR
            )
        except Exception as e:
            await status_msg.answer(f"❌ Failed to start process for P{p}: {e}")
            continue

        last_update_time = time.time()
        last_percentage = 0.0
        current_text = ""
        
        async def flush_ui(text: str, force: bool = False):
            nonlocal last_update_time, current_text
            current_time = time.time()
            if force or (current_time - last_update_time) >= 3.0:
                full_text = f"📥 **Downloading P{p}** ({i+1}/{len(pages)})\n{text}"
                if full_text != current_text:
                    try:
                        await status_msg.edit_text(full_text, parse_mode="Markdown")
                        current_text = full_text
                        last_update_time = current_time
                    except Exception as e:
                        pass

        buffer = bytearray()
        while True:
            chunk = await process.stdout.read(1024)
            if not chunk:
                break
                
            buffer.extend(chunk)
            
            while b'\r' in buffer or b'\n' in buffer:
                if b'\r' in buffer and b'\n' in buffer:
                    idx_r = buffer.find(b'\r')
                    idx_n = buffer.find(b'\n')
                    idx = min(idx_r, idx_n) if idx_r != -1 and idx_n != -1 else max(idx_r, idx_n)
                elif b'\r' in buffer:
                    idx = buffer.find(b'\r')
                else:
                    idx = buffer.find(b'\n')
                    
                line_bytes = buffer[:idx]
                del buffer[:idx+1]
                
                if not line_bytes:
                    continue
                    
                try:
                    decoded_line = line_bytes.decode('utf-8').strip()
                except UnicodeDecodeError:
                    decoded_line = line_bytes.decode('gbk', errors='ignore').strip()
                    
                if not decoded_line:
                    continue
            
                prog_match = PROGRESS_PATTERN.search(decoded_line)
                if prog_match:
                    try:
                        percentage = float(prog_match.group(1))
                        if abs(percentage - last_percentage) >= 5.0 or (time.time() - last_update_time) >= 3.0:
                            bar = create_progress_bar(percentage)
                            await flush_ui(f"`{bar}`", force=True)
                            last_percentage = percentage
                        elif percentage == 100.0:
                            await flush_ui(f"Processing...", force=True)
                    except ValueError:
                        pass
                    
        await process.wait()
        
        if process.returncode != 0:
            await status_msg.answer(f"❌ Download failed for P{p} with exit code {process.returncode}.")
            for f in dl_dir.glob("*"):
                try: os.remove(f)
                except: pass
            continue

        await flush_ui("Uploading file to Telegram...", force=True)
        
        # Give BBDown a moment to finish muxing and moving the file
        await asyncio.sleep(1.5)
        
        # BBDown nests files in subdirectories, use rglob and filter out images
        downloaded_files = [f for f in dl_dir.rglob("*") if f.is_file() and f.suffix.lower() not in ['.jpg', '.png']]
        if not downloaded_files:
            await status_msg.answer(f"❌ Error: Could not find downloaded file for P{p}.")
            # Cleanup dir tree
            try: shutil.rmtree(dl_dir)
            except: pass
            continue
            
        target_file = max(downloaded_files, key=lambda f: f.stat().st_size)
        
        try:
            file = FSInputFile(str(target_file))
            file_cap = f"{title} (P{p})"
            if target_file.suffix.lower() == ".mp4":
                await status_msg.answer_video(file, caption=file_cap)
            elif target_file.suffix.lower() in [".mp3", ".m4a", ".aac"]:
                await status_msg.answer_audio(file, caption=file_cap)
            else:
                await status_msg.answer_document(file, caption=file_cap)
                
            await flush_ui(f"✅ P{p} uploaded!", force=True)
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
            await status_msg.answer(f"❌ Failed to upload P{p} to Telegram: {e}")
        finally:
            try:
                shutil.rmtree(dl_dir)
            except:
                pass
                    
    await status_msg.answer(f"🎉 **Batch Download Complete!**\nFinished {len(pages)} items.", parse_mode="Markdown")
