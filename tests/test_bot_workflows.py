"""Cross-step bot regressions: use real routing and an offline Telegram transport."""
import asyncio
import pytest
from aiogram.types import Update, CallbackQuery
from sqlalchemy import select
from study.bot import router
from study.config import Settings
from study.groups import save_group
from study.models import Activity, Draft, Score, Task
from study.messages import bot_text
from test_bot import BotHarness


def test_individual_selection_only_becomes_variant_when_all_tasks_remain(db):
    async def run():
        h = BotHarness(db)
        for skip in (True, False):
            await h.text('/practice'); await h.press('sub:1'); await h.press('type:practice')
            if skip:
                await h.press(f"missing:{h.draft()['key'][:8]}:1")
            await h.next_exceptions(); await h.next_exceptions()
        with db.session() as s:
            records = list(s.scalars(select(Activity).order_by(Activity.id)))
            assert [r.kind for r in records] == ['practice', 'exam']
            assert {r.task_id for r in s.scalars(select(Score).where(Score.activity_id == records[0].id))} == {2, 3}
    asyncio.run(run())


def test_old_invitation_cancel_does_not_discard_current_practice(db):
    async def run():
        h = BotHarness(db)
        await h.text('/practice'); await h.press('sub:1'); await h.press('type:test')
        before = h.draft()
        invitation = h.message.model_copy(update={'message_id': before['message_id'] + 1000})
        await h.dp.feed_update(h.bot, Update(update_id=999, callback_query=CallbackQuery(
            id='old-invite', from_user=h.tg, chat_instance='x', message=invitation, data='home')))
        assert h.draft() == before
        await h.next_exceptions(); await h.next_exceptions()
        with db.session() as s:
            assert len(list(s.scalars(select(Activity)))) == 1
    asyncio.run(run())


def test_catalog_change_during_draft_stops_without_saving_wrong_scores(db):
    async def run():
        h = BotHarness(db)
        await h.text('/practice'); await h.press('sub:1'); await h.press('type:test')
        with db.session.begin() as s:
            s.get(Task, 2).max_score = 1
        await h.next_exceptions()
        with db.session() as s:
            assert s.get(Draft, 1) is None
            assert list(s.scalars(select(Activity))) == []
        assert h.transport.messages[-1].text == bot_text('errors.catalog_changed')
        await h.text('/practice'); await h.press('sub:1'); await h.press('type:test')
        await h.next_exceptions(); await h.next_exceptions()
        with db.session() as s:
            assert [r.score for r in s.scalars(select(Score).order_by(Score.task_id))] == [1, 1]
    asyncio.run(run())


def test_group_edit_keeps_the_tasks_already_chosen_for_draft(db):
    async def run():
        with db.session.begin() as s:
            group = save_group(s, 1, 'Моя группа', [1, 3]); s.flush(); group_id = group.id
        h = BotHarness(db)
        await h.text('/practice'); await h.press('sub:1')
        await h.press(f"group:{h.draft()['key'][:8]}:{group_id}")
        with db.session.begin() as s:
            from study.models import TaskGroup
            save_group(s, 1, 'Изменённая группа', [1, 2], s.get(TaskGroup, group_id))
        await h.next_exceptions()
        buttons = [b.callback_data for row in h.transport.messages[-1].reply_markup.inline_keyboard for b in row]
        prefix = f"mistake:{h.draft()['key'][:8]}:"
        assert prefix + '3' in buttons and prefix + '2' not in buttons
        await h.next_exceptions()
        with db.session() as s:
            assert {r.task_id for r in s.scalars(select(Score))} == {1, 3}
    asyncio.run(run())


@pytest.mark.parametrize('stage', ['subject', 'type', 'group-skipped', 'group-errors', 'score', 'date'])
@pytest.mark.parametrize('command', ['/help', '/goals', '/weekly', '/rest', '/start'])
def test_read_commands_and_rest_preserve_each_input_stage(db, stage, command):
    async def run():
        h = BotHarness(db)
        await h.text('/practice')
        if stage != 'subject':
            await h.press('sub:1')
        if stage in ('group-skipped', 'group-errors', 'score'):
            await h.press('type:test')
        if stage in ('group-errors', 'score'):
            await h.next_exceptions()
        if stage == 'score':
            await h.press(f"mistake:{h.draft()['key'][:8]}:2")
            await h.next_exceptions()
        if stage == 'date':
            await h.press('date')
        before = h.draft()
        assert before['stage'] == stage
        h.dp = router(db, Settings())
        await h.text(command)
        assert h.draft() == before
        with db.session() as s:
            assert list(s.scalars(select(Activity))) == []
    asyncio.run(run())


@pytest.mark.parametrize('invalid', ['', 'завтра', '32.10.2026', '29.02.2025', '19.10.2026'])
def test_invalid_date_retains_draft_and_valid_date_can_follow(db, monkeypatch, invalid):
    from datetime import date
    monkeypatch.setattr('study.bot.local_day', lambda user: date(2026, 10, 18))
    async def run():
        h = BotHarness(db)
        await h.text('/practice'); await h.press('sub:1'); await h.press('date')
        before = h.draft()
        await h.text(invalid)
        assert h.draft() == before
        await h.text('17.10.2026')
        assert h.draft()['day'] == '2026-10-17' and h.draft()['stage'] == 'type'
        await h.press('type:test'); await h.next_exceptions(); await h.next_exceptions()
        with db.session() as s:
            assert s.scalar(select(Activity)).day == date(2026, 10, 17)
    asyncio.run(run())


def test_interacting_again_reactivates_notifications_after_unblocking(db):
    from study.models import User
    async def run():
        with db.session.begin() as s:
            s.get(User, 1).bot_active = False
        h = BotHarness(db)
        await h.text('/practice')
        with db.session() as s:
            assert s.get(User, 1).bot_active
    asyncio.run(run())


def test_pre_update_individual_draft_does_not_keep_false_variant_kind(db):
    async def run():
        h = BotHarness(db)
        await h.text('/practice'); await h.press('sub:1'); await h.press('type:practice')
        await h.press(f"missing:{h.draft()['key'][:8]}:1")
        with db.session.begin() as s:
            row = s.get(Draft, 1)
            old = dict(row.data); old.pop('catalog', None); old['kind'] = 'exam'
            row.data = old
        h.dp = router(db, Settings())
        await h.next_exceptions(); await h.next_exceptions()
        with db.session() as s:
            assert s.scalar(select(Activity)).kind == 'practice'
            assert {r.task_id for r in s.scalars(select(Score))} == {2, 3}
    asyncio.run(run())


def test_deleted_bot_message_is_replaced_and_input_can_continue(db):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText
    async def run():
        h = BotHarness(db)
        await h.text('/practice')
        old_id = h.draft()['message_id']
        original = h.transport.make_request
        failed = False
        async def deleted(bot, method, timeout=None):
            nonlocal failed
            if isinstance(method, EditMessageText) and not failed:
                failed = True
                raise TelegramBadRequest(method=method, message='message to edit not found')
            return await original(bot, method, timeout)
        h.transport.make_request = deleted
        await h.press('sub:1')
        assert failed and h.draft()['message_id'] != old_id
        await h.press('type:test'); await h.next_exceptions(); await h.next_exceptions()
        with db.session() as s:
            assert len(list(s.scalars(select(Activity)))) == 1
            assert s.get(Draft, 1) is None
    asyncio.run(run())


def test_telegram_rate_limit_preserves_draft_and_delivers_in_order(db):
    from datetime import datetime, UTC, timedelta
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendMessage
    from study.models import BotOutbox
    from study.responses import drain_outbox
    async def run():
        h = BotHarness(db)
        original = h.transport.make_request
        failed = False
        async def rate_limit(bot, method, timeout=None):
            nonlocal failed
            if isinstance(method, SendMessage) and not failed:
                failed = True
                raise TelegramRetryAfter(method=method, message='retry later', retry_after=30)
            return await original(bot, method, timeout)
        h.transport.make_request = rate_limit
        await h.text('/practice')
        assert h.draft()['message_id'] < 0
        await h.text('/help')
        assert h.transport.messages == []
        with db.session() as s:
            assert [r.attempts for r in s.scalars(select(BotOutbox).order_by(BotOutbox.id))] == [1, 0]
        await drain_outbox(h.bot, db, now=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1))
        assert h.draft()['message_id'] > 0
        assert h.transport.messages[0].text == bot_text('entry.subject')
        assert '/practice' in h.transport.messages[1].text
        await h.press('sub:1'); await h.press('type:test')
        await h.next_exceptions(); await h.next_exceptions()
        with db.session() as s:
            assert len(list(s.scalars(select(Activity)))) == 1
            assert list(s.scalars(select(BotOutbox))) == []
    asyncio.run(run())


@pytest.mark.parametrize('change', ['maximum', 'new_task'])
@pytest.mark.parametrize('stage', ['date', 'score'])
def test_changed_catalog_cannot_save_from_text_or_points(db, change, stage):
    async def run():
        h = BotHarness(db)
        await h.text('/practice'); await h.press('sub:1')
        if stage == 'date':
            await h.press('date')
        else:
            await h.press('type:test'); await h.next_exceptions()
            await h.press(f"mistake:{h.draft()['key'][:8]}:2")
            await h.next_exceptions()
        with db.session.begin() as s:
            if change == 'maximum':
                s.get(Task, 2).max_score = 1
            else:
                s.add(Task(subject_id=1, name='Новое задание', part='none', max_score=1))
        if stage == 'date':
            await h.text('01.10.2026')
        else:
            await h.press(f"points:{h.draft()['key'][:8]}:0:1")
        with db.session() as s:
            assert s.get(Draft, 1) is None
            assert list(s.scalars(select(Activity))) == []
        assert h.transport.messages[-1].text == bot_text('errors.catalog_changed')
    asyncio.run(run())
