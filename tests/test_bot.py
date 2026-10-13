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
    user=User(gender='female')
    assert 'Сегодня отдыхала' in [b.text for row in menu(user).inline_keyboard for b in row]
