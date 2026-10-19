from types import SimpleNamespace
from study.avatars import fetch_avatar


def test_avatar_download_uses_bot_proxy_and_keeps_url_on_server(monkeypatch):
    calls = []
    class FakeBot:
        def __init__(self, token, session):
            calls.append(('token', token)); self.session = session
        async def __aenter__(self): return self
        async def __aexit__(self, *args): await self.session.close()
        async def get_user_profile_photos(self, user_id, limit):
            calls.append(('photos', user_id, limit))
            return SimpleNamespace(photos=[[SimpleNamespace(file_id='photo', file_size=100)]])
        async def get_file(self, file_id):
            return SimpleNamespace(file_path='photos/file.jpg')
        async def download_file(self, path, output, timeout):
            calls.append(('download', path, timeout)); output.write(b'\xff\xd8\xffimage')
    monkeypatch.setattr('study.avatars.Bot', FakeBot)
    settings = SimpleNamespace(bot_token='private-token', bot_proxy_url='')
    assert fetch_avatar(settings, 100) == (b'\xff\xd8\xffimage', 'image/jpeg')
    assert ('photos', 100, 1) in calls
    assert ('download', 'photos/file.jpg', 4) in calls


def test_private_or_missing_telegram_photo(monkeypatch):
    class FakeBot:
        def __init__(self, token, session): self.session = session
        async def __aenter__(self): return self
        async def __aexit__(self, *args): await self.session.close()
        async def get_user_profile_photos(self, *args, **kwargs):
            return SimpleNamespace(photos=[])
    monkeypatch.setattr('study.avatars.Bot', FakeBot)
    assert fetch_avatar(SimpleNamespace(bot_token='private-token', bot_proxy_url=''), 100) is None
