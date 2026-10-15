from datetime import date, timedelta
import pytest
from sqlalchemy import select
from study.models import User, Goal, Task, Subject, Score, Activity, Daily
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
        assert len(analytics(s, 2, 1)['wellbeing']) == 1
        s.get(User, 1).share_wellbeing = False
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


def test_weekly_counts_unmarked_days_as_rest(db):
    from study.services import weekly_text
    from study.messages import bot_text
    with db.session.begin() as s:
        # Two records on Monday are one study day; Tuesday has only a mood entry
        record(s, key='first')
        record(s, key='second')
        s.add(Daily(student_id=1, day=DAY + timedelta(days=1), mood=3))
        text = weekly_text(s, 1, DAY + timedelta(days=2))
        assert bot_text('weekly.study_days', study_days=1, rest_days=2) in text


def test_weekly_report_has_goal_cards_and_secondary_exam_scores(db):
    from study.services import weekly_text
    from study.messages import bot_text
    with db.session.begin() as s:
        s.add(Goal(student_id=1, subject_id=1, kind='task', task_id=1, week=DAY, target=2, series='weekly-goal'))
        record(s, 'practice', {1: 1}, 'weekly-practice')
        record(s, 'exam', {1: 1, 2: 2}, 'weekly-exam')
        s.get(Subject, 1).name = 'Математика <&>'
        text = weekly_text(s, 1, DAY + timedelta(days=3))
        assert text.startswith(bot_text('weekly.heading') + '\n\n' + bot_text('weekly.goals') + '\n')
        assert bot_text('weekly.goals') in text
        assert '🟩🟩🟩⬜️⬜️ 50% - Математика &lt;&amp;&gt;. №1' in text
        assert bot_text('weekly.statistics') + '\nДней с занятиями: 1\nДней отдыха: 3' in text
        assert bot_text('weekly.exams') in text
        assert 'Математика &lt;&amp;&gt;' in text and '(понедельник): 60' in text
        assert 'первичных' not in text and 'Самочувствие' not in text
        assert 'Выполнено:' not in text


def test_weekly_support_varies_by_week_and_matches_activity(db, monkeypatch):
    from study.services import weekly_text
    import study.services as services
    original = services.bot_texts
    def variants(key):
        if key.startswith('weekly.encouragement_'):
            return [key + ': первый', key + ': второй']
        return original(key)
    monkeypatch.setattr(services, 'bot_texts', variants)
    bot_texts = variants
    with db.session.begin() as s:
        empty = weekly_text(s, 1, DAY)
        rest_variants = bot_texts('weekly.encouragement_rest')
        active_variants = bot_texts('weekly.encouragement_active')
        assert empty.split('\n\n')[-1] in rest_variants
        assert weekly_text(s, 1, DAY) == empty
        assert weekly_text(s, 1, DAY + timedelta(days=7)).split('\n\n')[-1] != empty.split('\n\n')[-1]
        record(s, key='support')
        assert weekly_text(s, 1, DAY).split('\n\n')[-1] in active_variants
