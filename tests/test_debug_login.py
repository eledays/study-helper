from study.config import Settings
from study.web import create_app


def debug_client(database_url, enabled):
    app = create_app(Settings(secret_key='debug-test-secret-long-enough',
                              database_url=database_url, debug=enabled,
                              bot_username='', bot_token=''))
    app.config['TESTING'] = True
    return app.test_client()


def test_debug_login_disabled(db):
    client = debug_client(str(db.engine.url), False)
    assert 'Войти как первый пользователь' not in client.get('/login').text
    assert client.post('/auth/debug').status_code == 404


def test_debug_login_first_user_and_fresh_session(db):
    client = debug_client(str(db.engine.url), True)
    assert 'Войти как первый пользователь' in client.get('/login').text
    with client.session_transaction() as session:
        csrf = session['csrf']
        session['old_state'] = 'discard'
    response = client.post('/auth/debug', data={'csrf': csrf})
    assert response.status_code == 302
    assert response.location.endswith('/goals')
    with client.session_transaction() as session:
        assert session['user_id'] == 1
        assert session['csrf'] != csrf
        assert session.permanent
        assert 'old_state' not in session
    assert client.get('/goals').status_code == 200


def test_debug_login_requires_csrf(db):
    client = debug_client(str(db.engine.url), True)
    assert client.post('/auth/debug').status_code == 400
    with client.session_transaction() as session:
        assert 'user_id' not in session


def test_debug_login_empty_database(tmp_path):
    client = debug_client(f'sqlite:///{tmp_path}/empty.db', True)
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf']
    response = client.post('/auth/debug', data={'csrf': csrf}, follow_redirects=True)
    assert response.status_code == 200
    assert 'Пока нет пользователей для входа' in response.text
    with client.session_transaction() as session:
        assert 'user_id' not in session
