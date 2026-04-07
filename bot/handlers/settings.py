"""
bot/handlers/settings.py - 设置面板处理

包含：
- /settings 主菜单
- 登录管理
- 画质设置
- 菜单关闭
"""

import httpx
from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.config import is_admin, QUALITY_OPTIONS, DEFAULT_QUALITY
from bot.bilibili_api import get_auth_cookies, HEADERS
from bot.database import get_user_settings, set_user_settings

router = Router()
router.message.filter(lambda msg: msg.from_user is not None and is_admin(msg.from_user.id))
router.callback_query.filter(lambda c: c.from_user is not None and is_admin(c.from_user.id))


def get_settings_main_kb():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🔑 登录管理", callback_data="set_login_menu"))
    builder.row(types.InlineKeyboardButton(text="📋 订阅管理", callback_data="set_subs_list"))
    builder.row(types.InlineKeyboardButton(text="🎨 默认画质", callback_data="set_quality_menu"))
    builder.row(types.InlineKeyboardButton(text="❌ 关闭菜单", callback_data="close_menu"))
    return builder.as_markup()


@router.message(Command("settings"))
async def cmd_settings(message: types.Message, state: FSMContext):
    await state.clear()
    # 获取当前默认画质
    settings = await get_user_settings(message.chat.id)
    quality = settings.get("default_quality", DEFAULT_QUALITY) if settings else DEFAULT_QUALITY
    quality_name = QUALITY_OPTIONS.get(quality, "最高画质")
    
    await message.answer(
        f"⚙️ **机器人控制面板**\n"
        f"🎨 当前默认画质: **{quality_name}**\n\n"
        f"请选择你需要管理的功能：",
        reply_markup=get_settings_main_kb(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "close_menu")
async def cb_close_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()


@router.callback_query(F.data == "settings_main")
async def cb_settings_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    # 获取当前默认画质
    settings = await get_user_settings(callback.message.chat.id)
    quality = settings.get("default_quality", DEFAULT_QUALITY) if settings else DEFAULT_QUALITY
    quality_name = QUALITY_OPTIONS.get(quality, "最高画质")
    
    await callback.message.edit_text(
        f"⚙️ **机器人控制面板**\n"
        f"🎨 当前默认画质: **{quality_name}**\n\n"
        f"请选择你需要管理的功能：",
        reply_markup=get_settings_main_kb(),
        parse_mode="Markdown"
    )


# --- Quality Settings ---
@router.callback_query(F.data == "set_quality_menu")
async def cb_quality_menu(callback: types.CallbackQuery):
    # 获取当前设置
    settings = await get_user_settings(callback.message.chat.id)
    current_quality = settings.get("default_quality", DEFAULT_QUALITY) if settings else DEFAULT_QUALITY
    
    builder = InlineKeyboardBuilder()
    for qkey, qname in QUALITY_OPTIONS.items():
        marker = " ✓" if qkey == current_quality else ""
        builder.row(types.InlineKeyboardButton(
            text=f"{qname}{marker}",
            callback_data=f"set_quality_{qkey}"
        ))
    builder.row(types.InlineKeyboardButton(
        text="🔙 返回主菜单",
        callback_data="settings_main"
    ))
    
    current_name = QUALITY_OPTIONS.get(current_quality, "最高画质")
    await callback.message.edit_text(
        f"🎨 **默认画质设置**\n\n"
        f"当前: **{current_name}**\n\n"
        f"📖 **画质说明：**\n"
        f"• 最高画质：不限制，自动选择最佳\n"
        f"• 1080P/720P/480P/360P：限制最高分辨率\n"
        f"• 仅音频/弹幕/字幕：不下载视频\n\n"
        f"选择后，下载时默认使用此画质。\n"
        f"你仍可在每次下载时手动选择。",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("set_quality_"))
async def cb_set_quality(callback: types.CallbackQuery):
    quality = callback.data.replace("set_quality_", "")
    if quality not in QUALITY_OPTIONS:
        await callback.answer("无效的画质选项", show_alert=True)
        return
    
    await set_user_settings(callback.message.chat.id, {"default_quality": quality})
    quality_name = QUALITY_OPTIONS[quality]
    await callback.answer(f"✅ 已设置默认画质为: {quality_name}", show_alert=False)
    
    # 刷新画质菜单
    await cb_quality_menu(callback)


# --- Login Menu ---
@router.callback_query(F.data == "set_login_menu")
async def cb_login_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="🔄 查看登录状态 (检查失效)",
        callback_data="set_login_check"
    ))
    builder.row(types.InlineKeyboardButton(
        text="📱 发起扫码登录 (生成QR)",
        callback_data="set_login_trigger"
    ))
    builder.row(types.InlineKeyboardButton(
        text="🔙 返回主菜单",
        callback_data="settings_main"
    ))
    await callback.message.edit_text(
        "🔑 **登录管理**\n在此管理你的 Bilibili 登录凭证。",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "set_login_check")
async def cb_login_check(callback: types.CallbackQuery):
    await callback.answer("正在检查凭证有效性，请稍候...", show_alert=False)
    try:
        async with httpx.AsyncClient(
            headers=HEADERS,
            cookies=get_auth_cookies(),
            timeout=10.0
        ) as client:
            resp = await client.get("https://api.bilibili.com/x/web-interface/nav")
            data = resp.json()
            is_login = data.get("data", {}).get("isLogin", False)
        if is_login:
            uname = data["data"].get("uname", "未知用户")
            status = f"✅ **当前已登录**，账号：**{uname}**，凭证有效！"
        else:
            status = "❌ **未登录或凭证已失效**，请点击 [发起扫码登录] 重新认证。"
    except Exception as e:
        status = f"❌ **检查时发生错误**：{e}"

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="🔙 返回登录菜单",
        callback_data="set_login_menu"
    ))
    await callback.message.edit_text(
        f"🔑 **登录状态检查报告**\n\n{status}",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "set_login_trigger")
async def cb_login_trigger(callback: types.CallbackQuery):
    await callback.answer(
        "请发送 /login 到聊天框即可启动二维码生成。",
        show_alert=True
    )
