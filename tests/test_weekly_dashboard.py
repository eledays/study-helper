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
    assert 'id="completed-title"' in html
    assert html.count('goal-card completed-card') == 2
    assert '2 <span>заданий</span>' in html
    assert '13.10' in html and '14.10' in html
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


def test_repeated_sessions_share_one_compact_card(client, db):
    with db.session.begin() as s:
        for n in range(3):
            record_activity(s, 1, 1, 1, date(2026, 10, 13), 'practice', {1: 1}, f'compact-{n}')
    html = client.get('/goals?week=2026-10-12').text
    assert html.count('goal-card completed-card') == 1
    assert '3 <span>заданий</span>' in html
    assert '13.10 · 3 занятия' in html
    assert 'Сделано за неделю' not in html


def test_home_root_is_full_page_and_authorized_fragment(client):
    response = client.get('/')
    assert response.status_code == 200 and '>Главная</h1>' in response.text
    assert 'href="/?student=1' in response.text
    view = client.get('/?week=2026-10-12', headers={'X-Study-Navigation': '1'})
    assert view.status_code == 200 and view.json['url'] == '/?week=2026-10-12'
    assert '<!doctype' not in view.json['html']
    assert client.get('/?student=3').status_code == 403


def test_home_uses_only_aggregate_completion_data(client, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('Home must not allocate full analytics')
    monkeypatch.setattr('study.web.analytics', unexpected)
    assert client.get('/').status_code == 200
