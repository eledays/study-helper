from datetime import datetime
from zoneinfo import ZoneInfo
from study.bot import due_messages
from study.models import User, Slot, Delivery

def test_daily_weekly_and_reminder_timezone(db):
    with db.session.begin() as s:
        user = s.get(User, 1)
        user.evening='21:00'
        s.add(Slot(student_id=1, day=datetime(2026,10,18).date(), start='21:10', end='22:00',
                   title='Математика', subject_id=1, series='x'))
        s.flush()
        messages=list(due_messages(s,user,datetime(2026,10,18,18,0,tzinfo=ZoneInfo('UTC'))))
        assert len(messages)==3
        assert any(':daily:' in key for key,_,_ in messages)
        assert any(':weekly:' in key for key,_,_ in messages)
        user.reminders=False
        assert len(list(due_messages(s,user,datetime(2026,10,18,18,0,tzinfo=ZoneInfo('UTC')))))==2

# Exercise real aiogram routing without a Telegram token or network calls.
import asyncio
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, EditMessageReplyMarkup, EditMessageText
from aiogram.types import Update, Message, Chat, User as TelegramUser, CallbackQuery
from sqlalchemy import select
from study.bot import router
from study.config import Settings
from study.models import Draft, Activity, Score, Subject, Task, Daily

class FakeSession(BaseSession):
    def __init__(self):
        super().__init__(); self.messages=[]
    async def close(self): pass
    async def stream_content(self, *args, **kwargs):
        yield b''
    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, (SendMessage, EditMessageReplyMarkup, EditMessageText)):
            self.messages.append(method)
            return Message(message_id=getattr(method, 'message_id', None) or len(self.messages), date=datetime.now(), chat=Chat(id=100,type='private'), text=getattr(method,'text',None))
        return True

def test_bot_full_exam_duplicate_and_correction(db):
    async def run():
        transport=FakeSession(); bot=Bot('123456:ABCDEF_fake_token',session=transport)
        dp=router(db,Settings())
        tg=TelegramUser(id=100,is_bot=False,first_name='Саша')
        message=Message(message_id=1,date=datetime.now(),chat=Chat(id=100,type='private'),text='menu')
        counter=0
        async def press(data):
            nonlocal counter
            counter+=1
            await dp.feed_update(bot,Update(update_id=counter,callback_query=CallbackQuery(id=str(counter),from_user=tg,chat_instance='x',message=message,data=data)))
        await press('begin:exam'); await press('sub:1'); await press('exam-start')
        with db.session() as s: key=s.get(Draft,1).data['key'][:8]
        await press(f'points:{key}:0:1')
        await press(f'points:{key}:0:1') # Must not become score for task 2.
        with db.session() as s:
            assert s.get(Draft,1).data['index']==1
        await press(f'points:{key}:1:2'); await press(f'points:{key}:2:skip')
        # Restart dispatcher to verify the draft survives process restart.
        dp=router(db,Settings())
        with db.session() as session:
            assert session.get(Draft,1) is None
            assert len(list(session.scalars(select(Activity)))) == 1
        await press(f'points:{key}:2:skip')  # Repeated final answer must not create another record
        await press(f'save:{key}')  # Old confirmation buttons are harmless
        with db.session.begin() as s:
            activities=list(s.scalars(select(Activity)))
            assert len(activities)==1
            activity_id=activities[0].id
            assert activities[0].secondary==60
            s.get(Subject,1).scale={'2':99}
            s.get(Task,2).max_score=1
        await press(f'revise:{activity_id}')
        with db.session() as s:
            assert s.get(Draft,1) is None
            assert [sc.score for sc in s.scalars(select(Score).order_by(Score.task_id))] == [1,2,0]
        assert '/editing' in transport.messages[-1].text
    asyncio.run(run())

def test_delivery_persists_across_restart(db):
    from study.bot import deliver_due
    async def run():
        transport=FakeSession(); bot=Bot('123456:ABCDEF_fake_token',session=transport)
        with db.session.begin() as s: s.get(User,1).bot_active=True
        now=datetime(2026,10,18,18,0,tzinfo=ZoneInfo('UTC'))
        await deliver_due(bot,db,now)
        assert len(transport.messages)==2
        await deliver_due(bot,db,now)
        assert len(transport.messages)==2
    asyncio.run(run())

def test_optional_wellbeing_asked_once_and_dated(db):
    async def run():
        transport=FakeSession();bot=Bot('123456:ABCDEF_fake_token',session=transport)
        dp=router(db,Settings())
        tg=TelegramUser(id=100,is_bot=False,first_name='Саша')
        message=Message(message_id=1,date=datetime.now(),chat=Chat(id=100,type='private'),text='menu')
        async def press(data,n):
            await dp.feed_update(bot,Update(update_id=n,callback_query=CallbackQuery(id=str(n),from_user=tg,chat_instance='x',message=message,data=data)))
        await press('daily:2026-10-12:rest',1)
        before=len(transport.messages)
        await press('daily:2026-10-12:rest',2)
        assert len(transport.messages)==before+1
        await press('feel:2026-10-12:mood:3',3)
        with db.session() as s:
            daily=s.scalar(select(Daily))
            assert daily.day.isoformat()=='2026-10-12'
            assert daily.mood==3
    asyncio.run(run())

class BotHarness:
    def __init__(self, db):
        self.db=db
        self.transport=FakeSession()
        self.bot=Bot('123456:ABCDEF_fake_token',session=self.transport)
        self.dp=router(db,Settings())
        self.tg=TelegramUser(id=100,is_bot=False,first_name='Саша')
        self.message=Message(message_id=1,date=datetime.now(),chat=Chat(id=100,type='private'),text='menu')
        self.counter=0
    async def press(self, data):
        with self.db.session() as s:
            row = s.get(Draft, 1)
            if row and row.data.get('message_id'):
                self.message = self.message.model_copy(update={'message_id': row.data['message_id']})
        self.counter+=1
        await self.dp.feed_update(self.bot,Update(update_id=self.counter,callback_query=CallbackQuery(
            id=str(self.counter),from_user=self.tg,chat_instance='x',message=self.message,data=data)))
    async def text(self, text):
        self.counter+=1
        message=Message(message_id=self.counter,date=datetime.now(),chat=Chat(id=100,type='private'),from_user=self.tg,text=text)
        await self.dp.feed_update(self.bot,Update(update_id=self.counter,message=message))
    def draft(self):
        with self.db.session() as s:return dict(s.get(Draft,1).data)
    async def next_exceptions(self):
        data=self.draft()
        await self.press(f"exceptions-next:{data['key'][:8]}:{data['stage']}")


def test_test_part_defaults_and_missing_attempts(db):
    from study.models import Goal
    from study.services import progress
    async def run():
        h=BotHarness(db)
        await h.press('begin:practice');await h.press('sub:1');await h.press('type:test')
        assert h.draft()['values']=={'1':1,'2':2}
        key=h.draft()['key'][:8]
        await h.next_exceptions()
        # Duplicate Continue from the first screen cannot skip the error screen
        await h.press(f'exceptions-next:{key}:group-skipped')
        assert h.draft()['stage']=='group-errors'
        await h.next_exceptions()
        with db.session() as session: assert session.get(Draft,1) is None
        await h.press('begin:practice');await h.press('sub:1');await h.press('type:test')
        key=h.draft()['key'][:8]
        await h.press(f'missing:{key}:2');await h.next_exceptions()
        await h.press(f'mistake:{key}:1');await h.next_exceptions()
        with db.session.begin() as s:
            activities=list(s.scalars(select(Activity).order_by(Activity.id)))
            rows=list(s.scalars(select(Score).where(Score.activity_id==activities[1].id).order_by(Score.task_id)))
            assert [(sc.score,sc.attempted) for sc in rows]==[(0,True),(0,False)]
            s.add(Goal(student_id=1,subject_id=1,kind='test',week=activities[0].day-timedelta(days=activities[0].day.weekday()),target=2,series='x'))
            s.flush()
            assert progress(s,1,activities[0].day)[0]['done']==1
    from datetime import timedelta
    asyncio.run(run())


def test_group_errors_only_ask_for_partial_credit(db):
    async def run():
        h=BotHarness(db)
        await h.press('begin:practice');await h.press('sub:1');await h.press('type:test')
        key=h.draft()['key'][:8]
        await h.next_exceptions();await h.press(f'mistake:{key}:2');await h.next_exceptions()
        assert h.draft()['selected']==[2]
        await h.press(f'points:{key}:0:1')
        with db.session() as s:
            assert [sc.score for sc in s.scalars(select(Score).order_by(Score.task_id))]==[1,1]
    asyncio.run(run())


def test_exam_selection_bulk_and_total(db):
    from study.models import ExamTotal
    from study.services import analytics
    async def run():
        h=BotHarness(db)
        await h.press('begin:exam');await h.press('sub:1')
        key=h.draft()['key'][:8]
        await h.press(f'exam-select:{key}')
        await h.press(f'missing:{key}:3')
        await h.next_exceptions()
        await h.press(f'mistake:{key}:2')
        await h.next_exceptions()
        assert h.draft()['selected']==[2]  # One-point task does not need a score question
        await h.press(f'points:{key}:0:2')
        await h.press('begin:exam');await h.press('sub:1')
        key=h.draft()['key'][:8]
        await h.press(f'exam-bulk:{key}');await h.text('1 9 -')
        assert h.draft()['stage']=='exam-bulk'
        await h.text('0 1 -')
        await h.press('begin:exam');await h.press('sub:1')
        key=h.draft()['key'][:8]
        await h.press(f'exam-total:{key}');await h.text('8')
        assert h.draft()['stage']=='exam-total'
        await h.text('3')
        with db.session.begin() as s:
            report=analytics(s,1,1)['exams']
            assert [r['primary'] for r in report]==[3,1,3]
            assert report[2]['maximum']==7 and report[2]['tasks']==[]
            assert not any(button.text == 'Сохранить' for message in h.transport.messages
                           if getattr(message, 'reply_markup', None)
                           for row in message.reply_markup.inline_keyboard for button in row)
            activity_id=report[2]['id']
            s.get(Task,3).max_score=1
            s.get(Subject,1).scale={'2':99}
        await h.press(f'revise:{activity_id}');await h.text('2')
        with db.session() as session: assert session.get(Draft,1) is None
        with db.session() as s:
            assert s.get(ExamTotal,activity_id).maximum==7
            assert s.get(ExamTotal,activity_id).primary_score==3
            assert s.get(Activity,activity_id).secondary == 60
    asyncio.run(run())

def test_custom_group_can_mix_standard_parts(db):
    from study.groups import save_group
    async def run():
        with db.session.begin() as s:
            group=save_group(s,1,'Повторение',[1,3]);s.flush();group_id=group.id
        h=BotHarness(db)
        await h.press('begin:practice');await h.press('sub:1')
        key=h.draft()['key'][:8]
        await h.press(f'group:{key}:{group_id}')
        assert h.draft()['values']=={'1':1,'3':4}
        await h.next_exceptions();await h.next_exceptions()
        with db.session() as s:
            activity=s.scalar(select(Activity))
            assert activity.kind=='practice' and activity.title=='Повторение'
            assert {sc.task_id for sc in s.scalars(select(Score))}=={1,3}
    asyncio.run(run())


def test_welcome_has_no_buttons_and_working_command_help(db):
    async def run():
        h = BotHarness(db)
        await h.text('/start')
        sent = [message for message in h.transport.messages if isinstance(message, SendMessage)]
        assert len(sent) == 1
        assert sent[0].text.startswith('Привет 👋\nПомогу записывать занятия и следить за подготовкой\n\n')
        assert '/help' in sent[0].text and '{base_url}' not in sent[0].text
        assert sent[0].reply_markup is None
        await h.text('/help')
        help_message = h.transport.messages[-1]
        assert '/cancel' in help_message.text and 'http' in help_message.text
        assert help_message.reply_markup is None
        assert all(command not in help_message.text for command in ('/missed', '/history', '/site', '/menu'))
        assert 'Результаты сохраняются' not in help_message.text
        assert 'Внутри записи' not in help_message.text
        for command in ('/practice', '/exam', '/goals', '/weekly', '/rest'):
            assert command in help_message.text
        await h.text('/practice')
        assert h.draft()['stage'] == 'subject'
    asyncio.run(run())


def test_commands_route_without_consuming_score_input(db):
    async def run():
        h = BotHarness(db)
        await h.text('/exam')
        assert h.draft()['kind'] == 'exam'
        await h.press('sub:1')
        key = h.draft()['key'][:8]
        await h.press(f'exam-total:{key}')
        await h.text('/help')
        assert h.draft()['stage'] == 'exam-total'
        await h.text('3')
        with db.session() as s:
            assert s.get(Draft, 1) is None
            assert len(list(s.scalars(select(Activity)))) == 1
        await h.text('/goals')
        await h.text('/weekly')
        from study.messages import bot_text
        assert bot_text('weekly.heading') in h.transport.messages[-1].text
        await h.text('/rest')
        with db.session() as s:
            assert s.scalar(select(Daily)).status == 'study'  # A recorded activity keeps this a study day
        await h.text('/practice')
        await h.text('/cancel')
        with db.session() as s:
            assert s.get(Draft, 1) is None
    asyncio.run(run())


def test_practice_edits_one_message_selection_and_multiple_custom_mood_rows(db):
    async def run():
        h = BotHarness(db)
        await h.text('/practice')
        message_id = h.draft()['message_id']
        await h.press('sub:1')
        await h.press('type:test')
        key = h.draft()['key'][:8]
        first = h.transport.messages[-1]
        assert isinstance(first, EditMessageText) and first.message_id == message_id
        buttons = [b for row in first.reply_markup.inline_keyboard for b in row]
        assert [b.text for b in buttons[:2]] == ['✓ №1', '✓ №2']
        assert any(b.text == 'Снять выделение' for b in buttons)
        await h.press(f'selection:{key}:group-skipped:none')
        assert h.draft()['skipped'] == [1, 2]
        assert any(b.text == 'Выделить все' for row in h.transport.messages[-1].reply_markup.inline_keyboard for b in row)
        await h.next_exceptions()
        assert h.draft()['stage'] == 'group-skipped'  # An empty selection stays editable
        await h.press(f'selection:{key}:group-skipped:all')
        await h.press(f'selection:{key}:group-skipped:all')  # Duplicate is idempotent
        assert h.draft()['skipped'] == []
        await h.next_exceptions()
        assert h.draft()['errors'] == []
        await h.press(f'selection:{key}:group-skipped:none')  # Stale previous-step control
        assert h.draft()['skipped'] == []
        await h.press(f'selection:{key}:group-errors:all')
        assert h.draft()['errors'] == [1, 2]
        await h.press(f'selection:{key}:group-errors:none')
        await h.press(f'mistake:{key}:2')
        await h.next_exceptions()
        assert h.draft()['selected'] == [2]  # Only a multi-point error needs clarification
        await h.press(f'points:{key}:0:1')
        final = h.transport.messages[-1]
        assert isinstance(final, EditMessageText) and final.message_id == message_id
        assert final.text == 'Результат сохранен, отличная работа 💚\nКак ты себя чувствуешь?'
        assert [b.icon_custom_emoji_id for b in final.reply_markup.inline_keyboard[0]] == [
            '5346250607160209995', '5346036820868083187', '5348525299149584089',
            '5348309872179949637', '5348423147647414077']
        assert [len(row) for row in final.reply_markup.inline_keyboard] == [5, 5, 5]
        assert len([m for m in h.transport.messages if isinstance(m, SendMessage)]) == 1
        with db.session() as s:
            assert s.get(Draft, 1) is None
            assert [(r.score, r.attempted) for r in s.scalars(select(Score).order_by(Score.task_id))] == [(1, True), (1, True)]
        before_duplicate = len(h.transport.messages)
        await h.press(f'points:{key}:0:1')
        assert len(h.transport.messages) == before_duplicate
        mood_callback = final.reply_markup.inline_keyboard[0][2].callback_data
        await h.press(mood_callback)
        assert isinstance(h.transport.messages[-1], EditMessageText)
        assert h.transport.messages[-1].reply_markup is None
        with db.session() as s:
            assert s.scalar(select(Daily)).mood == 3
        assert len([m for m in h.transport.messages if isinstance(m, SendMessage)]) == 1
    asyncio.run(run())


def test_individual_tasks_use_same_defaults_and_resume_after_restart(db):
    async def run():
        h = BotHarness(db)
        await h.text('/practice')
        await h.press('sub:1')
        await h.press('type:practice')
        assert h.draft()['all_ids'] == [1, 2, 3]
        assert h.draft()['skipped'] == []
        key = h.draft()['key'][:8]
        await h.press(f'missing:{key}:1')
        h.dp = router(db, Settings())
        await h.next_exceptions()
        await h.next_exceptions()
        with db.session() as s:
            assert [(r.task_id, r.score) for r in s.scalars(select(Score).order_by(Score.task_id))] == [(2, 2), (3, 4)]
        assert len([m for m in h.transport.messages if isinstance(m, SendMessage)]) == 1
    asyncio.run(run())


def test_old_workflow_message_cannot_change_new_draft(db):
    async def run():
        h = BotHarness(db)
        await h.text('/practice')
        old = h.draft()
        await h.text('/practice')
        current = h.draft()
        old_message = h.message.model_copy(update={'message_id': old['message_id']})
        await h.dp.feed_update(h.bot, Update(update_id=1000, callback_query=CallbackQuery(
            id='stale', from_user=h.tg, chat_instance='x', message=old_message, data='sub:1')))
        assert h.draft()['key'] == current['key'] and h.draft()['stage'] == 'subject'
        await h.press('sub:1')
        assert h.draft()['stage'] == 'type'
    asyncio.run(run())


def test_practice_respects_disabled_wellbeing(db):
    async def run():
        with db.session.begin() as s:
            s.get(User, 1).wellbeing = False
        h = BotHarness(db)
        await h.text('/practice'); await h.press('sub:1'); await h.press('type:test')
        await h.next_exceptions(); await h.next_exceptions()
        final = h.transport.messages[-1]
        assert final.text == 'Результат сохранен, отличная работа 💚'
        assert final.reply_markup is None
    asyncio.run(run())


def test_custom_mood_confirmation_uses_utf16_offsets():
    from study.bot import mood_confirmation, mood_keyboard
    text, entities = mood_confirmation(3)
    entity, = entities
    encoded = text.encode('utf-16-le')
    assert encoded[entity.offset * 2:(entity.offset + entity.length) * 2].decode('utf-16-le') == '😐'
    assert entity.custom_emoji_id == '5348525299149584089'
    buttons = mood_keyboard('mood', '2026-10-15').inline_keyboard[0]
    assert [b.callback_data for b in buttons] == [f'feel:2026-10-15:mood:{n}' for n in range(1, 6)]
    assert all(b.icon_custom_emoji_id for b in buttons)


def test_mood_buttons_allow_plain_emoji_fallback(monkeypatch):
    import study.bot as bot_module
    original = bot_module.bot_texts
    monkeypatch.setattr(bot_module, 'bot_texts', lambda key: [''] * len(original('wellbeing.practice_moods')) if key == 'wellbeing.practice_mood_ids' else original(key))
    buttons = bot_module.mood_buttons('test')
    assert [b.text for b in buttons] == original('wellbeing.practice_moods')
    assert all(b.icon_custom_emoji_id is None for b in buttons)
    assert bot_module.mood_confirmation(1)[1] is None


def test_goals_have_percent_bars_and_escape_titles(db, monkeypatch):
    import study.bot as bot_module
    monkeypatch.setattr(bot_module, 'progress', lambda *args: [
        {'label': 'Алгебра <b>& задачи', 'percent': 0, 'done': 0, 'target': 5},
        {'label': 'Геометрия', 'percent': 15, 'done': 3, 'target': 20},
        {'label': 'Физика', 'percent': 100, 'done': 2, 'target': 2},
        {'label': 'Сверх плана', 'percent': 150, 'done': 3, 'target': 2},
    ])
    async def run():
        h = BotHarness(db)
        await h.text('/goals')
        sent = h.transport.messages[-1]
        assert sent.parse_mode == 'HTML'
        assert sent.text == ('<b>Цели недели</b>\n\nАлгебра &lt;b&gt;&amp; задачи 0%\n' + '⬜️' * 10
            + '\n\nГеометрия 15%\n' + '🟩' * 2 + '⬜️' * 8
            + '\n\nФизика 100%\n' + '🟩' * 10
            + '\n\nСверх плана 100%\n' + '🟩' * 10)
        await h.press('goals')
        assert h.transport.messages[-1].text == sent.text
    asyncio.run(run())


def test_rest_suppresses_only_todays_evening_preserving_work(db, monkeypatch):
    import study.bot as bot_module
    from study.services import record_activity
    from datetime import date
    today = date(2026, 10, 11)  # Sunday: weekly report must still arrive
    monkeypatch.setattr(bot_module, 'local_day', lambda user: today)
    async def run():
        with db.session.begin() as s:
            record_activity(s, 1, 1, 1, today, 'practice', {1: 1}, 'rest-check')
        h = BotHarness(db)
        await h.text('/rest')
        await h.text('/rest')
        with db.session() as s:
            assert len(list(s.scalars(select(Activity)))) == 1
            assert len(list(s.scalars(select(Score)))) == 1
            assert s.scalar(select(Daily)).status == 'study'
            user = s.get(User, 1)
            due = list(due_messages(s, user, datetime(2026, 10, 11, 18, tzinfo=ZoneInfo('UTC'))))
            assert not any(':daily:' in key for key, _, _ in due)
            assert any(':weekly:' in key for key, _, _ in due)
            tomorrow = list(due_messages(s, user, datetime(2026, 10, 12, 18, tzinfo=ZoneInfo('UTC'))))
            assert any(':daily:' in key for key, _, _ in tomorrow)
        await h.press('delete:1')
        with db.session() as s:
            assert len(list(s.scalars(select(Activity)))) == 1
        assert '/editing' in h.transport.messages[-1].text
    asyncio.run(run())


def test_no_button_menu_after_cancel_or_unknown_text_but_evening_has_actions(db):
    async def run():
        h = BotHarness(db)
        await h.text('/practice')
        await h.text('/cancel')
        with db.session() as s:
            assert s.get(Draft, 1) is None
        assert h.transport.messages[-1].reply_markup is None
        await h.text('Привет')
        assert h.transport.messages[-1].reply_markup is None
        assert '/help' in h.transport.messages[-1].text
        with db.session() as s:
            messages = list(due_messages(s, s.get(User, 1), datetime(2026, 10, 18, 18, tzinfo=ZoneInfo('UTC'))))
            assert all(markup is None for key, _, markup in messages if ':weekly:' in key)
            assert next(markup for key, _, markup in messages if ':daily:' in key) is not None
    asyncio.run(run())


def test_registration_write_access_sends_start_greeting(db):
    from aiogram.types import WriteAccessAllowed
    from study.messages import bot_text
    async def run():
        transport = FakeSession()
        bot = Bot('123456:ABCDEF_fake_token', session=transport)
        settings = Settings()
        dp = router(db, settings)
        message = Message(message_id=900, date=datetime.now(),
            chat=Chat(id=100, type='private'),
            from_user=TelegramUser(id=100, is_bot=False, first_name='Саша'),
            write_access_allowed=WriteAccessAllowed(from_request=True))
        await dp.feed_update(bot, Update(update_id=900, message=message))
        assert len(transport.messages) == 1
        assert transport.messages[0].text == bot_text('welcome.greeting', base_url=settings.base_url.rstrip('/'))
        with db.session() as s:
            assert s.get(User, 1).bot_active
    asyncio.run(run())


def test_reminder_records_slot_in_same_message_and_prevents_duplicates(db):
    async def run():
        from datetime import date, timedelta
        with db.session.begin() as s:
            slot = Slot(student_id=1, day=date.today()-timedelta(days=1), start='10:00', end='11:00',
                        title='Повторение', subject_id=1, task_ids=[1, 3], series='reminder')
            s.add(slot); s.flush(); slot_id = slot.id; day = slot.day
        h = BotHarness(db)
        await h.press(f'slot:{slot_id}')
        assert h.draft()['message_id'] == h.message.message_id
        assert h.draft()['day'] == day.isoformat()
        assert h.draft()['all_ids'] == [1, 3]
        await h.next_exceptions(); await h.next_exceptions()
        with db.session() as s:
            activity = s.scalar(select(Activity))
            assert activity.day == day and activity.title == 'Повторение'
            assert {r.task_id for r in s.scalars(select(Score))} == {1, 3}
        await h.press(f'slot:{slot_id}')
        with db.session() as s:
            assert len(list(s.scalars(select(Activity)))) == 1
            assert s.get(Draft, 1) is None
    asyncio.run(run())


def test_slot_without_subject_and_foreign_slot(db):
    async def run():
        from datetime import date
        with db.session.begin() as s:
            own = Slot(student_id=1, day=date.today(), start='10:00', end='11:00', title='Занятие', series='x')
            other = Slot(student_id=3, day=date.today(), start='10:00', end='11:00', title='Чужое', series='x')
            s.add_all([own, other]); s.flush(); own_id, other_id = own.id, other.id
        h = BotHarness(db)
        await h.press(f'slot:{other_id}')
        with db.session() as s: assert s.get(Draft, 1) is None
        await h.press(f'slot:{own_id}')
        assert h.draft()['stage'] == 'subject'
        await h.press('sub:1')
        assert h.draft()['title'] == 'Занятие'
    asyncio.run(run())


def test_reminders_include_result_buttons(db):
    from study.messages import bot_text
    with db.session.begin() as s:
        user = s.get(User, 1); user.evening = '10:00'; user.timezone = 'UTC'
        s.add(Slot(student_id=1, day=datetime(2026,10,19).date(), start='10:10', end='11:00', title='Пробник', kind='exam', subject_id=1, series='x'))
        s.flush()
        messages = list(due_messages(s, user, datetime(2026,10,19,10,0,tzinfo=ZoneInfo('UTC'))))
        assert len(messages) == 2
        daily, slot = messages
        assert daily[2].inline_keyboard[0][0].callback_data == 'daily:2026-10-19:practice'
        button = slot[2].inline_keyboard[0][0]
        assert button.callback_data.startswith('slot:') and button.text == bot_text('buttons.record_exam')


def test_study_suppresses_evening_and_rest_even_with_planned_rest(db):
    from datetime import date
    from study.services import record_activity, record_exam_total
    day = date(2026, 10, 18)
    with db.session.begin() as s:
        user = s.get(User, 1)
        user.timezone = 'Asia/Tokyo'
        user.evening = '01:00'
        s.add(Slot(student_id=1, day=day, start='01:10', end='02:00',
                   kind='rest', title='Отдых', series='rest'))
        # Yesterday's entry and another student's entry must not suppress today.
        record_activity(s, 1, 1, 1, date(2026, 10, 17), 'practice', {1: 1}, 'yesterday')
        s.add(Activity(student_id=3, subject_id=1, day=day, kind='practice', request_key='other'))
        s.flush()
        now = datetime(2026, 10, 17, 16, tzinfo=ZoneInfo('UTC'))  # Oct 18 locally
        assert any(':daily:' in key for key, _, _ in due_messages(s, user, now))
        record_exam_total(s, 1, 1, 1, day, 3, 'today-exam')
        messages = list(due_messages(s, user, now))
        assert not any(':daily:' in key for key, _, _ in messages)
        assert any(':weekly:' in key for key, _, _ in messages)
        assert not any(markup and any(':rest' in b.callback_data for row in markup.inline_keyboard for b in row)
                       for _, _, markup in messages)
        tomorrow = list(due_messages(s, user, datetime(2026, 10, 18, 16, tzinfo=ZoneInfo('UTC'))))
        assert any(':daily:' in key for key, _, _ in tomorrow)


def test_stale_rest_button_after_study_keeps_study_and_removes_keyboard(db, monkeypatch):
    from datetime import date
    from study.services import record_activity
    from study.messages import bot_text
    monkeypatch.setattr('study.bot.local_day', lambda user: date(2026, 10, 18))
    async def run():
        with db.session.begin() as s:
            record_activity(s, 1, 1, 1, date(2026, 10, 18), 'practice', {1: 1}, 'studied')
        h = BotHarness(db)
        await h.press('daily:2026-10-18:rest')
        assert isinstance(h.transport.messages[-2], EditMessageReplyMarkup)
        assert h.transport.messages[-2].reply_markup is None
        assert h.transport.messages[-1].text == bot_text('results.already_studied')
        await h.text('/rest')
        assert h.transport.messages[-1].text == bot_text('results.already_studied')
        with db.session() as s:
            assert s.scalar(select(Daily)).status == 'study'
            assert s.get(Delivery, '1:daily:2026-10-18') is None
    asyncio.run(run())


def test_extra_moods_work_in_both_flows_without_changing_five_point_scale(db, monkeypatch):
    from datetime import date
    from study.bot import mood_keyboard, mood_options, mood_score
    from study.services import record_activity
    monkeypatch.setattr('study.bot.local_day', lambda user: date(2026, 10, 18))
    rows = mood_keyboard('mood', '2026-10-18').inline_keyboard
    assert [len(row) for row in rows] == [5, 5, 5, 1]
    assert len(mood_options()) == 15
    assert {mood_score(n) for n in range(1, 16)} == {1, 2, 3, 4, 5}
    async def run():
        with db.session.begin() as s:
            record_activity(s, 1, 1, 1, date(2026, 10, 18), 'practice', {1: 1}, 'mood-extra')
            s.scalar(select(Daily)).wellbeing_asked = True
        h = BotHarness(db)
        await h.press('result-feel:1:15')
        assert h.transport.messages[-1].entities[0].custom_emoji_id == mood_options()[14][1]
        with db.session() as s:
            assert s.scalar(select(Daily)).mood == 5
        await h.press('feel:2026-10-18:mood:8')
        with db.session() as s:
            assert s.scalar(select(Daily)).mood == 1
        before = len(h.transport.messages)
        await h.press('result-feel:1:16')
        await h.press('feel:2026-10-18:load:6')
        assert len(h.transport.messages) == before
    asyncio.run(run())
