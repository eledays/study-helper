"""Fetch a fresh Telegram profile photo without exposing the bot file URL."""
import asyncio
from io import BytesIO
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession


async def telegram_avatar(settings, telegram_id):
    async with Bot(settings.bot_token, session=AiohttpSession(proxy=settings.bot_proxy_url or None, timeout=4)) as bot:
        photos = await bot.get_user_profile_photos(telegram_id, limit=1)
        if not photos.photos:
            return None
        photo = photos.photos[0][0]  # Smallest size is enough for the profile chip.
        if photo.file_size and photo.file_size > 2 * 1024 * 1024:
            return None
        file = await bot.get_file(photo.file_id)
        if not file.file_path:
            return None
        output = BytesIO()
        await bot.download_file(file.file_path, output, timeout=4)
        data = output.getvalue()
        if len(data) > 2 * 1024 * 1024:
            return None
        if data.startswith(b'\xff\xd8\xff'):
            return data, 'image/jpeg'
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            return data, 'image/png'
        return None


def fetch_avatar(settings, telegram_id):
    return asyncio.run(asyncio.wait_for(telegram_avatar(settings, telegram_id), timeout=10))
