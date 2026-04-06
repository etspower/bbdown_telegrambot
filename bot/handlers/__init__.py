"""
bot/handlers/__init__.py - Handlers 包入口

统一导出所有 handlers，便于 main.py 导入。
"""

from aiogram import Router

from .commands import router as commands_router
from .settings import router as settings_router
from .subscription import router as subscription_router
from .download import router as download_router

router = Router()
router.include_router(commands_router)
router.include_router(settings_router)
router.include_router(subscription_router)
router.include_router(download_router)
