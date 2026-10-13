from datetime import datetime
from zoneinfo import ZoneInfo
from study.bot import due_messages, menu
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

def test_gender():
    user=User(gender='female',timezone='Europe/Moscow')
    assert 'Сегодня отдыхала' in [b.text for row in menu(user).inline_keyboard for b in row]

# Exercise real aiogram routing without a Telegram token or network calls.
import asyncio
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, EditMessageReplyMarkup
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
        if isinstance(method, (SendMessage, EditMessageReplyMarkup)):
            self.messages.append(method)
            return Message(message_id=len(self.messages), date=datetime.now(), chat=Chat(id=100,type='private'), text=getattr(method,'text',None))
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
        await press(f'save:{key}'); await press(f'save:{key}')
        with db.session.begin() as s:
            activities=list(s.scalars(select(Activity)))
            assert len(activities)==1
            activity_id=activities[0].id
            assert activities[0].secondary==60
            s.get(Subject,1).scale={'2':99}
            s.get(Task,2).max_score=1
        await press(f'revise:{activity_id}')
        with db.session() as s: key=s.get(Draft,1).data['key'][:8]
        await press(f'points:{key}:0:0'); await press(f'points:{key}:1:2'); await press(f'points:{key}:2:skip')
        await press(f'save:{key}')
        with db.session() as s:
            scores=list(s.scalars(select(Score).order_by(Score.task_id)))
            assert [sc.score for sc in scores]==[0,2,0]
            assert scores[1].maximum==2
            assert s.get(Activity,activity_id).secondary is None # Old scale has no primary=2.
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
