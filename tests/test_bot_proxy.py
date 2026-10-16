import asyncio

import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp_socks import ProxyError

from study.config import Settings


def test_telegram_uses_socks_proxy_and_remote_dns():
    async def check():
        destinations = []

        async def reject_connection(reader, writer):
            try:
                version, count = await reader.readexactly(2)
                assert version == 5
                await reader.readexactly(count)
                writer.write(b'\x05\x00')
                await writer.drain()
                assert await reader.readexactly(4) == b'\x05\x01\x00\x03'
                length = (await reader.readexactly(1))[0]
                host = (await reader.readexactly(length)).decode()
                port = int.from_bytes(await reader.readexactly(2), 'big')
                destinations.append((host, port))
                writer.write(b'\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(reject_connection, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        settings = Settings(_env_file=None, bot_proxy_url=f'socks5://127.0.0.1:{port}')
        session = AiohttpSession(proxy=settings.bot_proxy_url or None)
        bot = Bot('123456:fake', session=session)
        try:
            async with server:
                with pytest.raises(ProxyError):
                    await bot.get_me(request_timeout=2)
            assert destinations == [('api.telegram.org', 443)]
        finally:
            await session.close()

    asyncio.run(check())
