from datetime import date
import json
import re
from study.services import record_activity, record_exam_total
from study.bot import weekly_link


def test_completed_dashboard_groups_only_selected_students_week(client, db):
    with db.session.begin() as s:
        record_activity(s, 1, 1, 1, date(2026, 10, 13), 'practice', {1: 1, 2: 2}, 'dashboard-study')
        record_exam_total(s, 1, 1, 1, date(2026, 10, 14), 3, 'dashboard-exam')
        record_activity(s, 1, 1, 1, date(2026, 10, 20), 'practice', {1: 1}, 'next-week')
    html = client.get('/goals?week=2026-10-12').text
    assert 'Сделано за неделю' in html
    assert 'Занятий: 1 · Пробников: 1 · Решений заданий: 2' in html
    assert 'Решено заданий: 2' in html
    assert 'Вторник' in html and 'Среда' in html
    assert 'Редактирование записей' in client.get('/settings').text


def test_weekly_link_filters_exact_week_and_keeps_access_checks(client, db):
    with db.session.begin() as s:
        record_activity(s, 1, 1, 1, date(2026, 10, 13), 'practice', {1: 1}, 'inside')
        record_activity(s, 1, 1, 1, date(2026, 10, 19), 'practice', {1: 1}, 'outside')
    link = weekly_link(date(2026, 10, 18), 'https://study.example/')
    assert link.inline_keyboard[0][0].url == 'https://study.example/analytics?period=7&week=2026-10-12'
    response = client.get('/analytics?period=7&week=2026-10-12')
    assert response.status_code == 200
    data = json.loads(re.search(r'id="analytics-data">(.*?)</script>', response.text, re.S).group(1))
    assert [r['day'] for r in data['rhythm']] == ['2026-10-13']
    assert 'data-today="2026-10-18"' in response.text
    assert client.get('/analytics?period=7&week=2026-10-12&student=3').status_code == 403
