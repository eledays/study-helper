from datetime import date, timedelta
import pytest
from sqlalchemy import select
from study.models import Goal, Task, Subject, Score, Activity, Daily
from study.services import record_activity, progress, monday, analytics, change_series, parse_scale

DAY = date(2026, 10, 12)

def record(s, kind='practice', values=None, key='one'):
    return record_activity(s, 1, 1, 1, DAY, kind, values or {3: 0}, key)

def test_zero_score_counts_and_exam_separate(db):
    with db.session.begin() as s:
        s.add_all([Goal(student_id=1, subject_id=1, kind='second', week=DAY, target=5, series='a'),
                   Goal(student_id=1, subject_id=1, task_id=3, kind='task', week=DAY, target=5, series='b'),
                   Goal(student_id=1, subject_id=1, kind='test', week=DAY, target=5, series='c')])
        record(s)
        record(s, 'exam', {1: 1, 2: 2, 3: None}, 'exam')
        record(s, 'test', {1: 0, 2: 0}, 'test')
        assert [r['done'] for r in progress(s, 1, DAY)] == [1, 1, 1]

def test_snapshot_and_missing_scale(db):
    with db.session.begin() as s:
        a = record(s, 'exam', {1: 1, 2: 2}, 'exam')
        assert a.secondary == 60
        s.get(Task, 2).max_score = 5
        s.get(Subject, 1).scale = {'3': 90}
        report = analytics(s, 1, 1)['exams'][0]
        assert report['secondary'] == 60
        assert report['maximum'] == 7
        assert report['tasks'][2]['attempted'] is False
        b = record(s, 'exam', {1: 0, 2: 1}, 'other')
        assert b.secondary is None

def test_access_privacy_and_no_teacher_edit(db):
    with db.session.begin() as s:
        s.add(Daily(student_id=1, day=DAY, mood=2, load=4))
        record(s)
        assert analytics(s, 2, 1)['wellbeing'] == []
        assert len(analytics(s, 1, 1)['wellbeing']) == 1
        with pytest.raises(PermissionError): analytics(s, 3, 1)
        with pytest.raises(PermissionError): record_activity(s, 2, 1, 1, DAY, 'practice', {3: 4}, 'bad')

def test_idempotency_and_bounds(db):
    with db.session.begin() as s:
        a = record(s)
        b = record(s)
        assert a.id == b.id
        assert len(list(s.scalars(select(Activity)))) == 1
    with db.session.begin() as s:
        with pytest.raises(ValueError): record(s, values={3: 5}, key='invalid')
        s.rollback()

def test_partial_test_not_full(db):
    with db.session.begin() as s:
        with pytest.raises(ValueError): record(s, 'test', {1: 1})

def test_series_scope(db):
    with db.session.begin() as s:
        for n in range(3):
            s.add(Goal(student_id=1, subject_id=1, kind='second', week=DAY+timedelta(weeks=n), target=5, series='a'))
        s.flush()
        goals = list(s.scalars(select(Goal).order_by(Goal.week)))
        change_series(s, Goal, 2, 1, goals[1].id, 'future', {'target': 7})
        assert [x.target for x in goals] == [5, 7, 7]
        change_series(s, Goal, 2, 1, goals[1].id, 'all', {'target': 2})
        assert [x.target for x in goals] == [2, 2, 2]

@pytest.mark.parametrize('rows', [[('0','50'),('1','40')], [('1','10'),('1','20')], [('1','101')]])
def test_scale_validation(rows):
    with pytest.raises(ValueError): parse_scale(rows)
