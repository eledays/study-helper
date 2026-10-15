import asyncio
from datetime import date, datetime, UTC, timedelta
from html.parser import HTMLParser
import json
import re
import sqlite3

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage, EditMessageText
from sqlalchemy import select, text, event
from sqlalchemy.exc import OperationalError

from study.config import Settings
from study.db import Database
from study.manage import backup, restore, checked_connection, bot_health
from study.models import (User, Subject, Task, Activity, Score, Goal, GoalSelection,
                          BotOutbox, BotMessage, Delivery, Draft, TaskGroup)
from study.responses import drain_outbox
from study.services import record_activity, goal_cards
from study.telegram_text import split_text
from study.web import create_app
from test_bot import BotHarness


def production(**overrides):
    return Settings(_env_file=None, environment='production', debug=False, cookie_secure=True,
        base_url=overrides.pop('base_url', 'https://study.example.com'),
        secret_key=overrides.pop('secret_key', 'a-random-test-key-' * 4),
        bot_token='123456:fake', bot_username='test_bot', **overrides)


@pytest.mark.parametrize('change', [dict(debug=True), dict(cookie_secure=False),
    dict(base_url='http://example.com'), dict(base_url='https://user:pass@example.com'),
    dict(base_url='https://example.com/sub'), dict(secret_key='replace-with-a-long-random-value')])
def test_production_refuses_unsafe_config(change):
    settings = production().model_dump()
    settings.update(change)
    with pytest.raises(ValueError): Settings(_env_file=None, **settings)


def test_configuration_errors_do_not_expose_secrets():
    settings = production().model_dump()
    settings.update(debug=True, bot_token='SENSITIVE-TOKEN-MUST-NOT-BE-LOGGED')
    with pytest.raises(ValueError) as error:
        Settings(_env_file=None, **settings)
    assert 'SENSITIVE-TOKEN-MUST-NOT-BE-LOGGED' not in str(error.value)


def test_production_requires_migration_and_rejects_foreign_hosts(tmp_path):
    url = f'sqlite:///{tmp_path}/prod.db'
    settings = production(database_url=url)
    with pytest.raises(Exception): create_app(settings)
    Database(url).engine.dispose()
    app = create_app(settings)
    client = app.test_client()
    assert client.get('/health', base_url='https://study.example.com').status_code == 200
    assert client.post('/auth/debug', base_url='https://study.example.com').status_code == 404
    response = app.test_client().get('/login', base_url='https://study.example.com')
    assert response.headers['Strict-Transport-Security'] == 'max-age=31536000'
    assert 'Secure' in response.headers['Set-Cookie']
    assert client.get('/login', base_url='https://attacker.example').status_code == 400


def test_csrf_dates_headers_and_readiness(client):
    assert client.post('/subjects', data={'csrf': 'я'}).status_code == 400
    assert client.get('/goals?week=9999-12-31', follow_redirects=True).status_code == 200
    response = client.get('/settings')
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    engine = client.application.extensions['db'].engine
    def broken(*args):
        raise OperationalError('SELECT', {}, Exception('simulated failure'))
    event.listen(engine, 'before_cursor_execute', broken)
    try:
        assert client.get('/health').status_code == 503
        assert client.get('/live').status_code == 200
        assert client.get('/settings').status_code == 503
    finally:
        event.remove(engine, 'before_cursor_execute', broken)


def test_invalid_return_week_rolls_back(client, db):
    response = client.post('/subjects', data={'csrf':'test-csrf', 'name':'Do not commit', 'return_week':'invalid'})
    assert response.status_code == 302
    with db.session() as s:
        assert s.scalar(select(Subject).where(Subject.name == 'Do not commit')) is None


def test_history_pagination_and_server_period(client, db):
    with db.session.begin() as s:
        for n in range(55):
            s.add(Activity(student_id=1, subject_id=1, day=date(2026, 10, 12),
                kind='practice', request_key=f'page-{n}'))
        s.add(Activity(student_id=1, subject_id=1, day=date(2020, 1, 1), kind='practice', request_key='old'))
    first = client.get('/editing').text
    second = client.get('/editing?record_page=2').text
    assert first.count('class="record-editor"') == 50
    assert second.count('class="record-editor"') == 6
    assert 'Страница 1 из 2' in first
    payload = json.loads(re.search(r'id="analytics-data">(.*?)</script>', client.get('/analytics?period=28').text, re.S).group(1))
    assert len(payload['rhythm']) == 55
    assert all(r['day'] != '2020-01-01' for r in payload['rhythm'])


def test_confirmations_survive_network_failure_and_release_writer(db):
    async def run():
        h = BotHarness(db)
        await h.text('/practice'); await h.press('sub:1'); await h.press('type:practice')
        await h.next_exceptions()
        original = h.transport.make_request
        writes = []
        async def transport(bot, method, timeout=None):
            if isinstance(method, (SendMessage, EditMessageText)):
                # Another connection must be able to write during Telegram I/O.
                with sqlite3.connect(db.engine.url.database, timeout=.1) as conn:
                    conn.execute('INSERT INTO deliveries (key, sent_at) VALUES (?, ?)',
                        (f'parallel-{len(writes)}', '2026-10-15 00:00:00'))
                writes.append(True)
                raise TelegramNetworkError(method=method, message='simulated network failure')
            return await original(bot, method, timeout)
        h.transport.make_request = transport
        await h.next_exceptions()
        with db.session() as s:
            assert len(list(s.scalars(select(Activity)))) == 1
            assert s.get(Draft, 1) is None
            pending = list(s.scalars(select(BotOutbox)))
            assert len(pending) == 1 and pending[0].attempts == 1
        assert writes
        h.transport.make_request = original
        await asyncio.gather(*[drain_outbox(h.bot, db, now=datetime.now(UTC).replace(tzinfo=None)+timedelta(hours=1)) for _ in range(2)])
        with db.session() as s:
            assert list(s.scalars(select(BotOutbox))) == []
            assert len(list(s.scalars(select(Activity)))) == 1
        assert sum('Как самочувствие' in (getattr(msg, 'text', '') or '') for msg in h.transport.messages) <= 1
    asyncio.run(run())


def test_wellbeing_once_per_day_on_main_path(db):
    async def run():
        h = BotHarness(db)
        markups = []
        for _ in range(2):
            await h.text('/practice'); await h.press('sub:1'); await h.press('type:practice')
            await h.next_exceptions(); await h.next_exceptions()
            markups.append(h.transport.messages[-1].reply_markup)
        assert markups[0] is not None and markups[1] is None
    asyncio.run(run())


def test_blocked_bot_is_opt_out_not_failed_service(db):
    from aiogram.exceptions import TelegramForbiddenError
    async def run():
        h = BotHarness(db)
        with db.session.begin() as s: s.get(User,1).bot_active = True
        original = h.transport.make_request
        async def blocked(bot, method, timeout=None):
            if isinstance(method, SendMessage):
                raise TelegramForbiddenError(method=method, message='blocked by user')
            return await original(bot, method, timeout)
        h.transport.make_request = blocked
        await h.text('/goals')
        with db.session() as s:
            assert s.get(User,1).bot_active is False
            assert list(s.scalars(select(BotOutbox))) == []
    asyncio.run(run())


def test_long_html_goals_are_bounded_and_tags_balanced(db):
    class Validator(HTMLParser):
        def __init__(self): super().__init__(); self.stack=[]; self.plain=''
        def handle_starttag(self, tag, attrs): self.stack.append(tag)
        def handle_endtag(self, tag): assert self.stack.pop() == tag
        def handle_data(self, value): self.plain += value
    original = '<b>' + ('&lt;тест&gt; 🟩\n' * 1500) + '</b>'
    chunks = split_text(original, html=True)
    assert len(chunks) > 1
    for chunk in chunks:
        parser = Validator(); parser.feed(chunk)
        assert not parser.stack
        assert len(parser.plain.encode('utf-16-le'))//2 <= 3500
    assert len(goal_cards([dict(label='Тест', percent=0, target=1000, done=0)])) < 100
    async def run():
        with db.session.begin() as s:
            from study.services import monday
            for n in range(150):
                s.add(Goal(student_id=1, subject_id=1, kind='test', week=monday(date.today()), target=1000, series=str(n)))
        h = BotHarness(db); await h.text('/goals')
        messages = [m for m in h.transport.messages if isinstance(m, SendMessage)]
        assert len(messages) > 1
        assert all(len(m.text.encode('utf-16-le'))//2 < 4096 for m in messages)
    asyncio.run(run())


def test_export_and_delete_only_own_account(client, db):
    with db.session.begin() as s:
        record_activity(s,1,1,1,date(2026,10,12),'exam',{1:1},'delete-own')
        s.get(User,3).teacher_id = 1
    response = client.get('/account/export?student=3')
    assert response.status_code == 200  # Export always refers to the signed-in user.
    payload = response.get_json()
    assert [row['id'] for row in payload['users']] == [1]
    assert payload['activities'][0]['request_key'] == 'delete-own'
    assert client.post('/account/delete', data={'csrf':'test-csrf','confirmation':'wrong'}).status_code == 302
    with db.session() as s: assert s.get(User,1)
    assert client.post('/account/delete', data={'csrf':'test-csrf','confirmation':'УДАЛИТЬ'}).status_code == 302
    with db.session() as s:
        assert s.get(User,1) is None
        assert s.get(User,2) is not None and s.get(User,3).teacher_id is None
        assert list(s.scalars(select(Activity))) == []
        assert list(s.scalars(select(Score))) == []
    assert client.get('/settings').location.endswith('/login')


def test_safe_catalog_deletion(client, db):
    with db.session.begin() as s:
        record_activity(s,1,1,1,date(2026,10,12),'practice',{1:1},'keep-history')
    assert client.post('/catalog/task/1/delete', data={'csrf':'test-csrf'}).status_code == 302
    assert client.post('/catalog/subject/1/delete', data={'csrf':'test-csrf'}).status_code == 302
    with db.session() as s: assert s.get(Task,1) and s.get(Subject,1)
    assert client.post('/catalog/task/3/delete', data={'csrf':'test-csrf'}).status_code == 302
    with db.session() as s: assert s.get(Task,3) is None


def test_delete_last_subject_does_not_recreate_defaults(client, db):
    assert client.post('/catalog/subject/1/delete', data={'csrf':'test-csrf'}).status_code == 302
    assert client.get('/goals').status_code == 200
    with db.session() as s:
        assert list(s.scalars(select(Subject).where(Subject.student_id == 1))) == []


def test_commit_failure_sends_no_success_and_leaves_draft(db, monkeypatch):
    async def run():
        h = BotHarness(db)
        await h.text('/practice'); await h.press('sub:1'); await h.press('type:practice')
        await h.next_exceptions()
        count = len(h.transport.messages)
        from sqlalchemy.orm import Session
        def fail_commit(self): raise OperationalError('COMMIT', {}, Exception('simulated disk failure'))
        with monkeypatch.context() as patch:
            patch.setattr(Session, 'commit', fail_commit)
            with pytest.raises(OperationalError): await h.next_exceptions()
        with db.session() as s:
            assert list(s.scalars(select(Activity))) == []
            assert s.get(Draft, 1) is not None
        assert len(h.transport.messages) == count
    asyncio.run(run())


def test_concurrent_writers_and_readers(db):
    from concurrent.futures import ThreadPoolExecutor
    from study.models import Daily
    from study.services import analytics
    with db.session.begin() as s: s.add(Daily(student_id=1, day=date(2026,10,12)))
    def work(number):
        with db.session.begin() as s:
            if number % 3:
                record_activity(s,1,1,1,date(2026,10,12),'practice',{1:1},f'concurrent-{number}')
            else:
                analytics(s,1,1,start=date(2026,10,1),end=date(2026,10,15),summaries=False)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(work, range(45)))
    with db.session() as s:
        assert len(list(s.scalars(select(Activity)))) == 30


def test_backup_restore_including_wal_and_no_overwrite(db, tmp_path):
    archive = tmp_path/'backup.db'
    with db.session.begin() as s:
        record_activity(s,1,1,1,date(2026,10,12),'practice',{1:1},'backup-data')
    backup(str(db.engine.url), archive)
    assert archive.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): backup(str(db.engine.url), archive)
    restored_url = f'sqlite:///{tmp_path}/restored.db'
    restore(restored_url, archive)
    restored = Database(restored_url, initialize=False)
    with restored.session() as s:
        assert s.scalar(select(Activity)).request_key == 'backup-data'
    checked_connection(archive).close()


def test_schema_version_refuses_downgrade(db):
    with db.engine.begin() as conn: conn.execute(text('UPDATE schema_version SET version=999'))
    with pytest.raises(RuntimeError): Database(str(db.engine.url))
    with pytest.raises(RuntimeError): Database(str(db.engine.url), initialize=False)


def test_bot_health_requires_polling_and_scheduler(db, tmp_path):
    heartbeat, polling = tmp_path/'scheduler', tmp_path/'polling'
    heartbeat.touch()
    with pytest.raises(RuntimeError): bot_health(db, heartbeat, polling)
    polling.touch(); bot_health(db, heartbeat, polling)
    with db.session.begin() as s:
        s.add(BotOutbox(chat_id=100, method='send_message', payload={'text':'test'}, failed=True))
    with pytest.raises(RuntimeError): bot_health(db, heartbeat, polling)
