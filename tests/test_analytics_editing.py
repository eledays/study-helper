from datetime import date
import json
import re
from sqlalchemy import select
from study.messages import bot_text
from study.models import Activity, Score, Task, Subject, ExamTotal
from study.services import analytics, record_activity, record_exam_total

DAY = date(2026, 10, 12)


def test_analytics_keeps_skips_and_historical_maxima(db):
    with db.session.begin() as s:
        record_activity(s, 1, 1, 1, DAY, 'practice', {1: 1, 2: 1, 3: 0}, 'practice-stats')
        record_activity(s, 1, 1, 1, DAY, 'exam', {1: None, 2: 2, 3: None}, 'exam-stats')
        s.get(Task, 2).max_score = 10
        report = analytics(s, 1, 1)
        practice = next(row for row in report['tasks'] if row['id'] == 2 and row['kind'] == 'practice')
        assert practice['percent'] == 50
        assert practice['results'][0]['maximum'] == 2
        skipped = next(row for row in report['tasks'] if row['id'] == 1 and row['kind'] == 'exam')
        assert skipped['attempts'] == 0
        assert skipped['results'][0]['attempted'] is False
        assert len(report['rhythm']) == 2
        assert report['exams'][0]['secondary'] is None
        assert report['exams'][0]['subject_id'] == 1


def test_analytics_and_editing_are_separate(client, db):
    with db.session.begin() as s:
        activity = record_activity(s, 1, 1, 1, DAY, 'exam', {1: 1, 2: 2}, 'ui-edit')
        activity_id = activity.id
    html = client.get('/analytics').text
    assert '<h1>' not in html
    assert 'weekly-chart' not in html and 'История занятий' not in html
    assert f'action="/activity/{activity_id}' not in html
    assert 'id="exam-subject"' in html and 'id="task-subject"' in html
    payload = json.loads(re.search(r'id="analytics-data">(.*?)</script>', html, re.S).group(1))
    assert payload['exams'][0]['secondary'] == 60
    editing = client.get('/editing').text
    assert f'/activity/{activity_id}/editor?' in editing
    assert 'class="record-form"' not in editing
    editor = client.get(f'/activity/{activity_id}/editor').json['html']
    assert f'action="/activity/{activity_id}"' in editor
    assert f'action="/activity/{activity_id}/delete"' in editor
    settings = client.get('/settings').text
    assert 'action="/scale/' not in settings and 'action="/groups' not in settings
    subjects = client.get('/editing?tab=subjects').text
    assert 'action="/subjects/1"' in subjects and 'action="/scale/1"' in subjects
    assert 'action="/groups' in subjects


def test_edit_scores_preserves_snapshots_and_updates_analytics(client, db):
    with db.session.begin() as s:
        activity = record_activity(s, 1, 1, 1, DAY, 'exam', {1: 1, 2: 2, 3: None}, 'edit-snapshot')
        activity_id = activity.id
        s.get(Task, 2).max_score = 1
        s.get(Subject, 1).scale = {'3': 99}
    response = client.post(f'/activity/{activity_id}', data={
        'csrf': 'test-csrf', 'day': '2026-10-11', 'title': 'Исправление',
        'score_1': '1', 'score_2': '2', 'score_3': '', 'secondary': '',
        'return_section': 'editing', 'return_tab': 'works'})
    assert response.status_code == 302 and '/editing?' in response.location and 'tab=works' in response.location
    with db.session() as s:
        activity = s.get(Activity, activity_id)
        assert activity.day == date(2026, 10, 11) and activity.title == 'Исправление'
        assert activity.secondary == 60
        rows = list(s.scalars(select(Score).where(Score.activity_id == activity_id).order_by(Score.task_id)))
        assert rows[1].maximum == 2 and rows[1].score == 2
        assert rows[2].attempted is False


def test_edit_total_and_explicit_converted_score(client, db):
    with db.session.begin() as s:
        activity = record_exam_total(s, 1, 1, 1, DAY, 3, 'edit-total')
        activity_id = activity.id
        s.get(Task, 2).max_score = 1
    response = client.post(f'/activity/{activity_id}', data={
        'csrf': 'test-csrf', 'day': str(DAY), 'primary': '5', 'secondary': '72'})
    assert response.status_code == 302
    with db.session() as s:
        assert s.get(ExamTotal, activity_id).maximum == 7
        assert s.get(ExamTotal, activity_id).primary_score == 5
        assert analytics(s, 1, 1)['exams'][0]['secondary'] == 72


def test_invalid_edit_rolls_back_and_permissions_hold(client, db):
    with db.session.begin() as s:
        activity = record_activity(s, 1, 1, 1, DAY, 'practice', {1: 1, 2: 2}, 'invalid-edit')
        activity_id = activity.id
    values = {'csrf': 'test-csrf', 'day': '2026-10-11', 'title': 'Не сохранять', 'score_1': '0', 'score_2': '3', 'return_section': 'editing'}
    response = client.post(f'/activity/{activity_id}', data=values, follow_redirects=True)
    assert bot_text('errors.score_range') in response.text
    with db.session() as s:
        assert s.get(Activity, activity_id).day == DAY
        assert [r.score for r in s.scalars(select(Score).where(Score.activity_id == activity_id).order_by(Score.task_id))] == [1, 2]
    assert client.post(f'/activity/{activity_id}', data={**values, 'student': 3}).status_code == 403
    with client.session_transaction() as session:
        session['user_id'] = 2
    assert client.get('/editing?student=1').status_code == 200
    assert f'action="/activity/{activity_id}"' not in client.get('/editing?student=1').text
    assert client.post(f'/activity/{activity_id}', data={**values, 'student': 1}).status_code == 403
    assert client.post(f'/activity/{activity_id}/delete', data={'csrf': 'test-csrf', 'student': 1}).status_code == 403


def test_plan_editor_preserves_tab_and_week(client, db):
    from study.models import Goal
    with db.session.begin() as s:
        goal = Goal(student_id=1, subject_id=1, kind='test', week=DAY, target=2, series='new-edit')
        s.add(goal)
        s.flush()
        goal_id = goal.id
    response = client.post(f'/goals/{goal_id}', data={
        'csrf': 'test-csrf', 'target': 6, 'scope': 'one', 'return_section': 'editing',
        'return_tab': 'plan', 'return_week': str(DAY)})
    assert '/editing?' in response.location and 'tab=plan' in response.location and f'week={DAY}' in response.location
    assert f'action="/goals/{goal_id}"' not in client.get('/goals?week=2026-10-12').text
    assert f'action="/goals/{goal_id}"' in client.get(response.location).text


def test_automatic_conversion_recalculates_after_score_edit(client, db):
    with db.session.begin() as s:
        s.get(Subject, 1).scale = {'2': 45, '3': 60}
        activity = record_activity(s, 1, 1, 1, DAY, 'exam', {1: 1, 2: 2}, 'auto-conversion')
        activity_id = activity.id
    response = client.post(f'/activity/{activity_id}', data={
        'csrf': 'test-csrf', 'day': str(DAY), 'score_1': '0', 'score_2': '2', 'score_3': '',
        'conversion': 'auto', 'secondary': '60'})
    assert response.status_code == 302
    with db.session() as s:
        assert s.get(Activity, activity_id).secondary == 45


def test_task_numbers_are_in_natural_order(client, db):
    with db.session.begin() as s:
        s.add(Task(subject_id=1, name='№10', part='none', max_score=1))
    html = client.get('/analytics').text
    payload = json.loads(re.search(r'id="analytics-data">(.*?)</script>', html, re.S).group(1))
    assert [row['name'] for row in payload['task_catalog']] == ['№1', '№2', '№10', '№19']
