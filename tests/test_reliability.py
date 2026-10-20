import hashlib
import hmac
import time

from study.config import Settings
from study.web import create_app


def test_telegram_login_is_bound_to_browser_and_state_is_consumed(db):
    token = 'synthetic-test-token'
    app = create_app(Settings(_env_file=None, secret_key='login-regression-secret-long-enough',
        database_url=str(db.engine.url), bot_token=token, bot_username='test_bot'))
    victim, attacker = app.test_client(), app.test_client()
    victim.get('/login')
    attacker.get('/login')
    with victim.session_transaction() as state:
        victim_state = state['login_state']
        state['user_id'] = 1
    with attacker.session_transaction() as state:
        attacker_state = state['login_state']
    payload = {'id': '200', 'first_name': 'Teacher', 'auth_date': str(int(time.time()))}
    check = '\n'.join(f'{key}={value}' for key, value in sorted(payload.items()))
    payload['hash'] = hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    for state in ('', attacker_state, 'неверный'):
        assert victim.get('/auth/telegram', query_string={**payload, 'state': state}).status_code == 403
        with victim.session_transaction() as session:
            assert session['user_id'] == 1
    assert victim.get('/auth/telegram', query_string={**payload, 'state': victim_state}).status_code == 302
    with victim.session_transaction() as session:
        assert session['user_id'] == 2 and 'login_state' not in session
    assert victim.get('/auth/telegram', query_string={**payload, 'state': victim_state}).status_code == 403
    assert 'state=' in attacker.get('/login').text


def test_spa_validation_rolls_back_without_success_redirect(client, db):
    from study.models import User
    response = client.post('/profile', headers={'X-Study-Navigation': '1'}, data={
        'csrf': 'test-csrf', 'return_section': 'settings', 'name': 'Unsaved name',
        'gender': 'neutral', 'timezone': 'Europe/Mosco', 'evening': '19:00'})
    assert response.status_code == 422
    assert response.json == {'error': 'Неизвестный часовой пояс'}
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'Location' not in response.headers
    with db.session() as session:
        assert session.get(User, 1).name == 'Саша'


def test_spa_conflict_returns_validation_error(client):
    from flask import g
    from study.models import Task

    def concurrent_conflict():
        # Reproduce the constraint failure that can follow concurrent validation.
        g.db.add(Task(subject_id=1, name='№1', part='test', max_score=1))
        g.db.commit()
        return ''

    client.application.add_url_rule('/test-conflict', view_func=concurrent_conflict, methods=['POST'])
    response = client.post('/test-conflict', headers={'X-Study-Navigation': '1'}, data={'csrf': 'test-csrf'})
    assert response.status_code == 422
    assert 'Данные изменились' in response.json['error']
    assert client.get('/settings').status_code == 200


def test_navigation_icons_do_not_require_external_fonts(client):
    html = client.get('/goals').text
    assert 'fonts.googleapis.com' not in html and 'fonts.gstatic.com' not in html
    assert html.count('class="material-symbols-outlined section-icon"') == 5
    assert '<svg viewBox="0 0 24 24"' in html
