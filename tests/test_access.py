import asyncio
from datetime import datetime

from aiogram import Bot
from aiogram.types import Update, Message, Chat, User as TelegramUser, CallbackQuery, WriteAccessAllowed
from sqlalchemy import select, text

from study.access import access_allowed
from study.bot import router, deliver_due
from study.config import Settings
from study.db import Database
from study.models import AccessPolicy, AllowedTelegramUser, User, Draft, BotOutbox
from study.responses import drain_outbox
from test_bot import FakeSession


def enable(db):
    with db.session.begin() as s:
        s.add(AccessPolicy(id=1, whitelist_enabled=True))


def test_policy_default_and_membership(db):
    with db.session() as s:
        assert access_allowed(s, 999)
    enable(db)
    with db.session.begin() as s:
        assert not access_allowed(s, 999)
        s.add(AllowedTelegramUser(telegram_id=999))
    with db.session() as s:
        assert access_allowed(s, 999)
        assert not access_allowed(s, 100)


def test_existing_web_session_revoked_and_allowed(client, db):
    enable(db)
    assert client.get('/settings').status_code == 403
    with client.session_transaction() as session:
        assert 'user_id' not in session
        session['user_id'] = 1
    with db.session.begin() as s:
        s.add(AllowedTelegramUser(telegram_id=100))
    assert client.get('/settings').status_code == 200


def test_registration_checks_whitelist_before_creating_user(client, db, monkeypatch):
    monkeypatch.setattr('study.web.verify_telegram', lambda *args: True)
    with client.session_transaction() as session:
        session.clear()
    enable(db)
    client.get('/login')
    with client.session_transaction() as session:
        data = {'id': '999', 'first_name': 'New', 'state': session['login_state']}
    assert client.get('/auth/telegram', query_string=data).status_code == 403
    with db.session.begin() as s:
        assert s.scalar(select(User).where(User.telegram_id == 999)) is None
        s.add(AllowedTelegramUser(telegram_id=999))
    assert client.get('/auth/telegram', query_string=data).status_code == 302
    with db.session() as s:
        assert s.scalar(select(User).where(User.telegram_id == 999)) is not None


def test_bot_blocks_commands_service_messages_callbacks_and_notifications(db):
    enable(db)
    async def run():
        transport = FakeSession()
        bot = Bot('123456:ABCDEF_fake_token', session=transport)
        dp = router(db, Settings())
        tg = TelegramUser(id=999, is_bot=False, first_name='New')
        for number, content in enumerate(({'text': '/start'}, {'write_access_allowed': WriteAccessAllowed(from_request=True)}, {'text': '/practice'}), 1):
            message = Message(message_id=number, date=datetime.now(), chat=Chat(id=999, type='private'), from_user=tg, **content)
            await dp.feed_update(bot, Update(update_id=number, message=message))
        message = Message(message_id=4, date=datetime.now(), chat=Chat(id=999, type='private'), text='menu')
        await dp.feed_update(bot, Update(update_id=4, callback_query=CallbackQuery(id='denied', from_user=tg, chat_instance='x', message=message, data='begin:exam')))
        with db.session.begin() as s:
            assert s.scalar(select(User).where(User.telegram_id == 999)) is None
            assert s.scalar(select(Draft)) is None
            s.get(User, 1).bot_active = True
            s.add(BotOutbox(chat_id=100, method='send_message', payload={'chat_id': 100, 'text': 'queued'}))
        await drain_outbox(bot, db)
        await deliver_due(bot, db, datetime.now().astimezone())
        assert transport.messages == []
        with db.session.begin() as s:
            s.add(AllowedTelegramUser(telegram_id=999))
        await dp.feed_update(bot, Update(update_id=5, message=Message(message_id=5, date=datetime.now(), chat=Chat(id=999, type='private'), from_user=tg, text='/start')))
        assert len(transport.messages) == 1
    asyncio.run(run())


def test_whitelist_cli(db, monkeypatch, capsys):
    from study.manage import main
    monkeypatch.setenv('DATABASE_URL', str(db.engine.url))
    def command(*args):
        monkeypatch.setattr('sys.argv', ['manage', 'whitelist', *args])
        main()
        return capsys.readouterr().out
    assert 'выключен' in command('status')
    command('add', '999', '100', '999')
    assert '999' in command('list')
    command('enable')
    with db.session() as s:
        assert access_allowed(s, 999)
        assert not access_allowed(s, 200)
    command('remove', '999')
    with db.session() as s:
        assert not access_allowed(s, 999)
    command('disable')
    with db.session() as s:
        assert access_allowed(s, 999)


def test_migrate_v2_preserves_users_and_defaults_open(db):
    url = str(db.engine.url)
    with db.engine.begin() as conn:
        conn.execute(text('DROP TABLE allowed_telegram_users'))
        conn.execute(text('DROP TABLE access_policy'))
        conn.execute(text('UPDATE schema_version SET version=2'))
    migrated = Database(url)
    with migrated.session() as s:
        assert s.get(User, 1).telegram_id == 100
        assert access_allowed(s, 100)
    Database(url, initialize=False).engine.dispose()
